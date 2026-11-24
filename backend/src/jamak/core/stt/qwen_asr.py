"""Qwen3-ASR through a worker process in the separate PyTorch environment (installer.TorchComponent).

The backend exe carries no PyTorch, so this module only:
1. cuts the audio into speech chunks with the same Silero VAD faster-whisper uses,
2. runs runtime/qwen_asr_worker.py with the runtime's python (chunks, model folders, glossary context),
3. reads the result back, drops what Qwen is known to make up, and builds a Transcript.

Word timestamps come from Qwen3-ForcedAligner (character level for Japanese).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from jamak.config import SttOptions
from jamak.core.stt.base import ProgressFn
from jamak.core.types import SttSegment, Transcript, Word

log = logging.getLogger(__name__)

SR = 16000
_KANA = re.compile(r"[぀-ヿｦ-ﾟ]")
_PUNCT = re.compile(r"[\s、。！？!?…・「」『』（）()]+")
# what Qwen3-ASR answers for silence / noise-only chunks (seen on the benchmark film)
FILLERS = {"テスト", "テストテスト"}


class Cancelled(Exception):
    pass


class QwenWorkerError(RuntimeError):
    pass


def speech_chunks(samples, opts: SttOptions) -> list[tuple[float, float]]:
    """Silero VAD spans merged into chunks of at most `opts.max_chunk` seconds."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    max_len = max(5.0, min(float(opts.max_chunk), 60.0))
    if not opts.vad:
        total = len(samples) / SR
        return [(a, min(a + max_len, total)) for a in _frange(0.0, total, max_len)]
    spans = get_speech_timestamps(samples, VadOptions(threshold=opts.vad_threshold,
                                                      min_silence_duration_ms=opts.vad_min_silence_ms,
                                                      speech_pad_ms=200))
    merged: list[list[float]] = []
    for s in spans:
        a, b = s["start"] / SR, s["end"] / SR
        if merged and b - merged[-1][0] <= max_len and a - merged[-1][1] < 1.5:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out: list[tuple[float, float]] = []
    for a, b in merged:
        while b - a > max_len:
            out.append((round(a, 3), round(a + max_len, 3)))
            a += max_len
        if b - a > 0.2:
            out.append((round(a, 3), round(b, 3)))
    return out


def _frange(a: float, b: float, step: float):
    while a < b:
        yield a
        a += step


def clean(seg: dict) -> tuple[str, str | None]:
    """(text, reason it was dropped or None)."""
    text = (seg.get("text") or "").strip()
    norm = _PUNCT.sub("", text)
    if not norm:
        return "", "empty"
    if norm in FILLERS:
        return "", "filler"
    lang = (seg.get("language") or "").lower()
    if lang and "japanese" not in lang and not _KANA.search(text):
        return "", f"language:{lang}"  # Chinese (or other) output for Japanese audio
    return text, None


def to_segments(raw: list[dict]) -> tuple[list[SttSegment], list[dict]]:
    out: list[SttSegment] = []
    dropped: list[dict] = []
    for seg in raw:
        text, reason = clean(seg)
        if reason:
            dropped.append({"start": seg["start"], "end": seg["end"], "text": seg.get("text", ""), "reason": reason})
            continue
        words = [Word(start=float(w["start"]), end=max(float(w["end"]), float(w["start"])), text=w["text"], prob=1.0)
                 for w in seg.get("words") or [] if (w.get("text") or "").strip()]
        _restore_punctuation(words, text)
        out.append(SttSegment(start=float(seg["start"]), end=float(seg["end"]), text=text, words=words))
    return out, dropped


def _restore_punctuation(words: list[Word], text: str) -> None:
    """The aligner drops punctuation; put it back on the word before it so the segmenter can split
    at 。、？ the way it does for Whisper output."""
    if not words:
        return
    i = 0
    for w in words:
        j = text.find(w.text, i)
        if j < 0:
            continue
        i = j + len(w.text)
        k = i
        while k < len(text) and text[k] in "、。！？!?…，,":
            k += 1
        if k > i:
            w.text += text[i:k]
            i = k


def batch_for_vram(batch: int, free_gb: float | None = None) -> int:
    """Measured: model + aligner take ~6 GB, each chunk in a batch of 25 s about 0.2 GB more.
    Keep the batch small enough for the free VRAM (other apps, llama-server)."""
    if free_gb is None:
        free_gb = _free_vram_gb()
    if free_gb is None:
        return batch
    room = int((free_gb - 6.5) / 0.2)
    return max(1, min(batch, room))


def _free_vram_gb() -> float | None:
    import shutil

    if not shutil.which("nvidia-smi"):
        return None
    try:
        from jamak.core.proc import CREATE_NO_WINDOW

        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        return float(out.stdout.split()[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class QwenAsrRecognizer:
    def __init__(self, opts: SttOptions):
        self.opts = opts
        self._proc: subprocess.Popen | None = None

    def transcribe(
        self,
        audio: Path,
        initial_prompt: str | None,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Transcript:
        from jamak import models
        from jamak.core.audio import load_wav
        from jamak.core.proc import CREATE_NO_WINDOW, bind_to_lifetime
        from jamak.installer import runtime_file, torch_python

        s = models.require_stt(self.opts.model)
        dirs = models.local_dirs(s)
        assert dirs is not None
        samples = load_wav(audio)
        duration = len(samples) / SR
        chunks = speech_chunks(samples, self.opts)
        if progress:
            progress(0.0, f"말소리 구간 {len(chunks)}개")
        if not chunks:
            return Transcript(language="ja", duration=duration, model=self.opts.model, segments=[])

        with tempfile.TemporaryDirectory(prefix="jamak-qwen-") as tmp:
            req = Path(tmp) / "request.json"
            out = Path(tmp) / "result.json"
            req.write_text(json.dumps({
                "audio": str(audio), "chunks": chunks, "model_dir": str(dirs[0]), "aligner_dir": str(dirs[1]),
                "language": "Japanese", "context": initial_prompt or "", "batch_size": batch_for_vram(self.opts.batch_size),
                "out": str(out),
            }, ensure_ascii=False), encoding="utf-8")
            env_py = torch_python()
            self._proc = subprocess.Popen(
                [str(env_py), "-X", "utf8", "-I", str(runtime_file("qwen_asr_worker.py")), str(req)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW, env=_worker_env(),
            )
            bind_to_lifetime(self._proc)
            err_tail: list[str] = []
            threading.Thread(target=_drain, args=(self._proc.stderr, err_tail), daemon=True).start()
            watcher = None
            if cancel is not None:
                watcher = threading.Thread(target=self._kill_on, args=(cancel,), daemon=True)
                watcher.start()
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event") == "loaded" and progress:
                    progress(0.01, "Qwen3-ASR 모델을 올렸습니다")
                elif ev.get("event") == "progress" and progress:
                    progress(min(ev["done"] / max(ev["total"], 1), 0.999), ev.get("text", ""))
            code = self._proc.wait()
            self._proc = None
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if code != 0 or not out.exists():
                msg = " / ".join(t for t in err_tail[-4:] if t.strip())
                if "out of memory" in msg.lower():
                    msg = "GPU 메모리가 모자랍니다. 고급 설정에서 Qwen 배치 크기를 줄이세요. " + msg
                raise QwenWorkerError(f"Qwen3-ASR 실행 실패 (코드 {code}): {msg[-500:]}")
            raw = json.loads(out.read_text(encoding="utf-8"))["segments"]

        segments, dropped = to_segments(raw)
        if dropped:
            log.info("qwen-asr dropped %d chunks: %s", len(dropped),
                     ", ".join(sorted({d["reason"] for d in dropped})))
        return Transcript(language="ja", duration=duration, model=self.opts.model, segments=segments)

    def _kill_on(self, cancel: threading.Event) -> None:
        while self._proc is not None and self._proc.poll() is None:
            if cancel.wait(0.5):
                if self._proc is not None:
                    self._proc.kill()
                return

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
        self._proc = None


def _worker_env() -> dict[str, str]:
    import os

    from jamak.config import paths

    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))}
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": str(paths.models / "hf-home"),
                "PYTHONIOENCODING": "utf-8", "TOKENIZERS_PARALLELISM": "false"})
    return env


def _drain(stream, tail: list[str]) -> None:
    for line in stream:
        tail.append(line.rstrip())
        del tail[:-30]
