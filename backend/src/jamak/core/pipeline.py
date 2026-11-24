"""Run the stages in order, skipping those whose cached output is still valid.

audio -> separate -> stt -> segment -> translate -> export
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jamak.config import PipelineOptions, default_llm_path
from jamak.core import audio as audio_mod
from jamak.core import subtitle
from jamak.core.cache import Job, fingerprint, write_json_atomic
from jamak.core.filters import filter_segments
from jamak.core.segmenter import segment as segment_lines
from jamak.core.types import Lines, Transcript

STAGES = ["audio", "separate", "stt", "segment", "translate", "export"]
WEIGHTS = {"audio": 0.03, "separate": 0.12, "stt": 0.45, "segment": 0.02, "translate": 0.38, "export": 0.0}

EventFn = Callable[[dict[str, Any]], None]


class Cancelled(Exception):
    pass


@dataclass
class RunResult:
    job_key: str
    job_dir: Path
    outputs: list[Path]
    timings: dict[str, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def initial_prompt(opts: PipelineOptions) -> str | None:
    terms = [g.ja for g in opts.glossary if g.ja.strip()]
    if not terms:
        return None
    return "登場人物・用語：" + "、".join(terms[:40]) + "。"


class Pipeline:
    def __init__(self, video: Path, opts: PipelineOptions, on_event: EventFn | None = None,
                 cancel: threading.Event | None = None, jobs_root: Path | None = None):
        self.video = Path(video)
        self.opts = opts
        self.on_event = on_event or (lambda e: None)
        self.cancel = cancel or threading.Event()
        self.job = Job(self.video, jobs_root)
        self._active = [s for s in STAGES if s != "separate" or opts.separate.enabled]
        total = sum(WEIGHTS[s] for s in self._active) or 1.0
        self._weights = {s: WEIGHTS[s] / total for s in self._active}

    # ------------------------------------------------------------------ events
    def _overall(self, stage: str, frac: float) -> float:
        before = 0.0
        for s in self._active:
            if s == stage:
                break
            before += self._weights[s]
        return round(min(1.0, before + self._weights.get(stage, 0) * frac), 4)

    def emit(self, stage: str, status: str, progress: float = 0.0, message: str = "", **extra: Any) -> None:
        self.on_event({
            "job": self.job.key, "stage": stage, "status": status,
            "progress": round(progress, 4), "overall": self._overall(stage, progress if status != "done" else 1.0),
            "message": message, **extra,
        })

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    # ------------------------------------------------------------------ fingerprints
    def fingerprints(self) -> dict[str, str]:
        o = self.opts
        fps: dict[str, str] = {}
        fps["audio"] = fingerprint({"track": o.audio.track, "sr": audio_mod.SAMPLE_RATE, "v": 1}, self.job.key)
        fps["separate"] = (fingerprint(o.separate, fps["audio"]) if o.separate.enabled else fps["audio"])
        fps["stt"] = fingerprint({"stt": o.stt.model_dump(mode="json"), "prompt": initial_prompt(o), "v": 1},
                                 fps["separate"])
        fps["segment"] = fingerprint({"filters": o.filters.model_dump(mode="json"),
                                      "segment": o.segment.model_dump(mode="json"),
                                      "songs": o.songs.model_dump(mode="json"), "v": 1}, fps["stt"])
        t = o.translate
        if t.mode == "managed":
            mp = Path(t.model_path) if t.model_path else default_llm_path()
            model_id: Any = {"file": mp.name if mp else None, "size": mp.stat().st_size if mp and mp.exists() else 0}
        else:
            model_id = {"url": t.base_url, "name": t.model_name}
        fps["translate"] = fingerprint({
            "model": model_id, "batch": t.batch_size, "ctx": t.context_lines, "temp": t.temperature,
            "parallel": t.parallel > 1, "glossary": [g.model_dump() for g in o.glossary], "v": 1,
        }, fps["segment"])
        return fps

    # ------------------------------------------------------------------ run
    def run(self, force_from: str | None = None) -> RunResult:
        fps = self.fingerprints()
        forced = set(STAGES[STAGES.index(force_from):]) if force_from else set()
        result = RunResult(job_key=self.job.key, job_dir=self.job.dir, outputs=[])

        def stage(name: str, artifacts: list[str], fn: Callable[[], dict[str, Any] | None]) -> None:
            if name not in self._active:
                return
            self._check_cancel()
            if name not in forced and artifacts and self.job.is_fresh(name, fps[name], *artifacts):
                result.skipped.append(name)
                self.emit(name, "skipped", 1.0, "cached")
                return
            self.emit(name, "start", 0.0)
            t0 = time.perf_counter()
            extra = fn() or {}
            elapsed = time.perf_counter() - t0
            result.timings[name] = round(elapsed, 2)
            if artifacts:
                self.job.mark_done(name, fps[name], elapsed, **extra)
            self.emit(name, "done", 1.0, f"{elapsed:.1f}s", elapsed=round(elapsed, 2))

        stage("audio", ["audio.wav"], self._audio)
        stage("separate", ["vocals.wav"], self._separate)
        stage("stt", ["transcript.json"], self._stt)
        stage("segment", ["segments.ja.json"], self._segment)
        stage("translate", ["segments.ko.json"], lambda: self._translate(fps["segment"], result))

        def _export() -> None:
            lines = self.load_lines()
            result.outputs = subtitle.export(lines.lines, self.video, self.opts.output)
            self.job.update_meta(last_export=[str(p) for p in result.outputs])

        stage("export", [], _export)
        return result

    # ------------------------------------------------------------------ stages
    def _audio(self) -> dict[str, Any]:
        info = audio_mod.probe(self.video)
        track = audio_mod.pick_track(info, self.opts.audio.track)
        self.emit("audio", "progress", 0.2, f"track {track.index} ({track.language or 'und'}, {track.codec})")
        audio_mod.extract_wav(self.video, self.job.path("audio.wav"), track)
        self.job.update_meta(duration=info.duration, tracks=[t.__dict__ for t in info.tracks])
        return {"track": track.index, "language": track.language}

    def _separate(self) -> None:
        from jamak.core import separate as sep

        audio_info = self.job.stage_info("audio") or {}
        try:
            sep.separate_vocals(
                self.video, int(audio_info.get("track", 0)), self.job.path("vocals.wav"), self.opts.separate,
                duration=float(self.job.meta.get("duration") or 0.0),
                progress=lambda f, m: self.emit("separate", "progress", f, m), cancel=self.cancel,
            )
        except sep.Cancelled as e:
            raise Cancelled() from e

    def _stt_input(self) -> Path:
        return self.job.path("vocals.wav") if self.opts.separate.enabled else self.job.path("audio.wav")

    def _stt(self) -> dict[str, Any]:
        from jamak.core.stt import create_recognizer

        rec = create_recognizer(self.opts.stt)
        try:
            self.emit("stt", "progress", 0.0, f"loading {self.opts.stt.model}")
            tr = rec.transcribe(self._stt_input(), initial_prompt(self.opts),
                                progress=lambda f, m: self.emit("stt", "progress", f, m), cancel=self.cancel)
        except Exception as e:
            if type(e).__name__ == "Cancelled":
                raise Cancelled() from e
            raise
        finally:
            rec.close()  # free GPU memory before translation
        write_json_atomic(self.job.path("transcript.json"), tr)
        return {"segments": len(tr.segments), "device": getattr(rec, "device", None)}

    def _segment(self) -> dict[str, Any]:
        tr = Transcript.model_validate_json(self.job.path("transcript.json").read_text(encoding="utf-8"))
        kept, dropped = filter_segments(tr.segments, self.opts.filters)
        lines = apply_song_ranges(segment_lines(kept, self.opts.segment), self.opts.songs)
        write_json_atomic(self.job.path("segments.ja.json"), Lines(lines=lines))
        write_json_atomic(self.job.path("filtered.json"), dropped)
        return {"lines": len(lines), "dropped": len(dropped)}

    def _translate(self, segment_fp: str, result: RunResult) -> dict[str, Any]:
        lines = Lines.model_validate_json(self.job.path("segments.ja.json").read_text(encoding="utf-8")).lines
        # keep the user's edits if segmentation has not changed since they were made
        ko_path = self.job.path("segments.ko.json")
        prev = self.job.stage_info("translate") or {}
        if ko_path.exists() and prev.get("segment_fp") == segment_fp:
            old = {ln.id: ln for ln in Lines.model_validate_json(ko_path.read_text(encoding="utf-8")).lines}
            for ln in lines:
                o = old.get(ln.id)
                if o and o.locked and o.ja == ln.ja:
                    ln.ko, ln.locked = o.ko, True
        stats = run_translation(lines, self.opts, self.job,
                                progress=lambda f, m: self.emit("translate", "progress", f, m),
                                cancel=self.cancel)
        write_json_atomic(ko_path, Lines(lines=lines))
        result.stats["translate"] = stats
        return {"segment_fp": segment_fp, **{k: stats[k] for k in ("requests", "failed_lines", "completion_tokens")}}

    # ------------------------------------------------------------------ helpers
    def load_lines(self) -> Lines:
        p = self.job.path("segments.ko.json")
        if not p.exists():
            p = self.job.path("segments.ja.json")
        return Lines.model_validate_json(p.read_text(encoding="utf-8"))


def apply_song_ranges(lines: list, songs) -> list:
    """Mark (or drop) lines that mostly fall inside a user-given song range."""
    if not songs.ranges:
        return lines
    out = []
    for ln in lines:
        dur = max(ln.end - ln.start, 1e-6)
        inside = sum(max(0.0, min(ln.end, b) - max(ln.start, a)) for a, b in songs.ranges)
        if inside / dur >= 0.5:
            if songs.mode == "skip":
                continue
            ln.flags = [*ln.flags, "song"]
        out.append(ln)
    for i, ln in enumerate(out):
        ln.id = i + 1
    return out


def run_translation(lines, opts: PipelineOptions, job: Job, progress=None, cancel=None,
                    only_ids: set[int] | None = None) -> dict[str, Any]:
    """Translate `lines` in place, starting/stopping llama-server when in managed mode."""
    from jamak.core.translate.llama_server import LlamaServer
    from jamak.core.translate.openai_compat import OpenAICompatTranslator, TranslationCancelled

    t = opts.translate
    server = None
    try:
        if t.mode == "external":
            if not t.base_url:
                raise ValueError("translate.base_url is required in external mode")
            base_url, model_name, api_key = t.base_url, t.model_name, t.api_key
        else:
            model = Path(t.model_path) if t.model_path else default_llm_path()
            if model is None:
                raise FileNotFoundError("no translation model (*.gguf) found in the models folder")
            if progress:
                progress(0.0, f"loading {model.name}")
            server = LlamaServer(model, t, log_path=job.path("llama-server.log"))
            base_url = server.start(cancel=cancel)
            model_name, api_key = "local", None
        tr = OpenAICompatTranslator(base_url, t, model_name, api_key)
        try:
            tr.translate(lines, opts.glossary, progress=progress, cancel=cancel, only_ids=only_ids)
        except TranslationCancelled as e:
            raise Cancelled() from e
        finally:
            tr.close()
        return dict(tr.stats)
    finally:
        if server is not None:
            server.stop()


def save_options(job: Job, opts: PipelineOptions) -> None:
    write_json_atomic(job.path("options.json"), json.loads(opts.model_dump_json()))
