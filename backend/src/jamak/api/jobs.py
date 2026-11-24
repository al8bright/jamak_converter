"""Job manager: one worker thread (one GPU), event fan-out to WebSocket subscribers."""

from __future__ import annotations

import asyncio
import json
import queue
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jamak.config import PipelineOptions, paths
from jamak.core.cache import Job, write_json_atomic
from jamak.core.pipeline import Cancelled, Pipeline, engine_summary, run_translation, save_options
from jamak.core.translate.factory import resolve_options
from jamak.core.types import Lines


@dataclass
class JobState:
    key: str
    video_path: str
    status: str = "queued"  # queued | running | done | error | cancelled
    kind: str = "run"  # run | retranslate
    stage: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    outputs: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    engine: dict[str, Any] | None = None  # translation profile summary (kind, model, cloud)
    usage: dict[str, Any] | None = None  # running token / cost counters of the translate stage
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    events: list[dict] = field(default_factory=list)

    def public(self, with_events: bool = False) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "events"}
        if with_events:
            d["events"] = self.events[-200:]
        return d


class JobManager:
    def __init__(self) -> None:
        self.states: dict[str, JobState] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.q: queue.Queue[tuple[str, Callable[[], None]]] = queue.Queue()
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._work, name="jamak-worker", daemon=True)
        self.worker.start()

    # ------------------------------------------------------------------ events
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def _publish(self, key: str, event: dict) -> None:
        event.setdefault("t", round(time.time(), 2))  # for the progress log
        st = self.states.get(key)
        if st is not None:
            st.events.append(event)
            if len(st.events) > 1000:
                del st.events[:500]
        subs = list(self.subscribers.get(key, ()))
        if self.loop and subs:
            for q in subs:
                self.loop.call_soon_threadsafe(q.put_nowait, event)

    def subscribe(self, key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.setdefault(key, set()).add(q)
        return q

    def unsubscribe(self, key: str, q: asyncio.Queue) -> None:
        self.subscribers.get(key, set()).discard(q)

    def _status(self, st: JobState, status: str, **kw: Any) -> None:
        st.status = status
        for k, v in kw.items():
            setattr(st, k, v)
        self._publish(st.key, {"job": st.key, "type": "status", "status": status, **st.public()})

    # ------------------------------------------------------------------ worker
    def _work(self) -> None:
        while True:
            key, fn = self.q.get()
            st = self.states[key]
            if self.cancels[key].is_set():
                self._status(st, "cancelled", finished_at=time.time())
                continue
            self._status(st, "running", started_at=time.time(), error=None)
            try:
                fn()
                self._status(st, "done", finished_at=time.time(), progress=1.0)
            except Cancelled:
                self._status(st, "cancelled", finished_at=time.time())
            except Exception as e:  # report, keep the worker alive
                traceback.print_exc()
                self._status(st, "error", finished_at=time.time(), error=f"{type(e).__name__}: {e}")

    def busy(self, key: str) -> bool:
        st = self.states.get(key)
        return bool(st and st.status in ("queued", "running"))

    def _new_state(self, key: str, video: str, kind: str) -> JobState:
        st = JobState(key=key, video_path=video, kind=kind)
        self.states[key] = st
        self.cancels[key] = threading.Event()
        return st

    def submit_run(self, video: Path, opts: PipelineOptions, force_from: str | None = None,
                   profile_id: int | None = None) -> JobState:
        job = Job(video)
        if self.busy(job.key):
            raise RuntimeError("this video is already queued or running")
        opts = resolve_options(opts, profile_id)
        save_options(job, opts)
        st = self._new_state(job.key, str(job.video), "run")
        st.engine = engine_summary(opts.translate)
        cancel = self.cancels[job.key]

        def on_event(e: dict) -> None:
            st.stage, st.progress, st.message = e["stage"], e["overall"], e.get("message", "")
            if e.get("usage"):
                st.usage = e["usage"]
            self._publish(job.key, {"type": "progress", **e})

        def run() -> None:
            pipe = Pipeline(video, opts, on_event=on_event, cancel=cancel)
            res = pipe.run(force_from=force_from)
            st.outputs = [str(p) for p in res.outputs]
            st.timings = res.timings
            st.stats = res.stats

        self.q.put((job.key, run))
        self._publish(job.key, {"job": job.key, "type": "status", "status": "queued", **st.public()})
        return st

    def submit_retranslate(self, key: str, ids: list[int], profile_id: int | None = None) -> JobState:
        """Retranslate some lines, optionally with another profile (options.json is not changed)."""
        if self.busy(key):
            raise RuntimeError("job is busy")
        job = Job.open_key(key)
        opts = load_options(job)
        if profile_id is not None:
            opts = resolve_options(opts, profile_id)
        st = self._new_state(key, str(job.video), "retranslate")
        st.engine = engine_summary(opts.translate)
        cancel = self.cancels[key]

        def run() -> None:
            path = job.path("segments.ko.json")
            if not path.exists():
                path = job.path("segments.ja.json")
            lines = Lines.model_validate_json(path.read_text(encoding="utf-8")).lines
            wanted = set(ids)
            for ln in lines:
                if ln.id in wanted:
                    ln.locked = False  # an explicit retranslate request overrides the edit lock

            def progress(f: float, m: str, usage: dict | None = None) -> None:
                st.stage, st.progress, st.message = "translate", f, m
                if usage:
                    st.usage = usage
                self._publish(key, {"type": "progress", "job": key, "stage": "translate", "status": "progress",
                                    "progress": f, "overall": f, "message": m, **({"usage": usage} if usage else {})})

            st.stats = {"translate": run_translation(lines, opts, job, progress=progress, cancel=cancel,
                                                     only_ids=wanted)}
            write_json_atomic(job.path("segments.ko.json"), Lines(lines=lines))

        self.q.put((key, run))
        return st

    def cancel(self, key: str) -> bool:
        ev = self.cancels.get(key)
        if ev is None:
            return False
        ev.set()
        return True


# ---------------------------------------------------------------------- job folder helpers


def load_options(job: Job) -> PipelineOptions:
    p = job.path("options.json")
    if p.exists():
        return PipelineOptions.model_validate_json(p.read_text(encoding="utf-8"))
    return PipelineOptions()


def list_job_dirs() -> list[dict[str, Any]]:
    out = []
    if not paths.jobs.exists():
        return out
    for d in paths.jobs.iterdir():
        meta_p = d / "meta.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        stages = meta.get("stages", {})
        engine = (stages.get("translate") or {}).get("engine")
        if engine is None:
            try:
                t = json.loads((d / "options.json").read_text(encoding="utf-8")).get("translate", {})
                engine = {"kind": t.get("mode"), "profile_name": t.get("profile_name"),
                          "cloud": t.get("mode") in ("anthropic", "gemini")}
            except (OSError, json.JSONDecodeError):
                engine = None
        out.append({
            "engine": engine,
            "key": d.name,
            "video_path": meta.get("video_path"),
            "video_name": meta.get("video_name"),
            "duration": meta.get("duration"),
            "updated": meta.get("updated"),
            "translated": "translate" in stages,
            "last_export": meta.get("last_export", []),
            "stages": {k: {"elapsed": v.get("elapsed"), "finished": v.get("finished")} for k, v in stages.items()},
        })
    out.sort(key=lambda m: m.get("updated") or "", reverse=True)
    return out


def purge_job(key: str) -> bool:
    d = paths.jobs / key
    if d.exists() and d.parent == paths.jobs:
        shutil.rmtree(d)
        return True
    return False
