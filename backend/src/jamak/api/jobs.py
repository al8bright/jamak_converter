"""Job manager: one worker thread (one GPU), event fan-out to WebSocket subscribers.

A job that the app was closed (or killed, or crashed) in the middle of is not resumed: `submit_run`
marks the job folder, the mark comes off when the job ends, and `purge_interrupted` deletes the
folders still marked at the next start.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jamak.config import PipelineOptions, paths
from jamak.core.cache import Job, _lock_for, write_json_atomic
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
        self.marked: dict[str, Job] = {}  # jobs carrying the "running" mark in meta.json
        self.closing = False
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
                self._unmark(key)
                self._status(st, "cancelled", finished_at=time.time())
                continue
            self._status(st, "running", started_at=time.time(), error=None)
            extra: dict[str, Any] = {}
            try:
                fn()
                status, extra = "done", {"progress": 1.0}
            except Cancelled:
                status = "cancelled"
            except Exception as e:  # report, keep the worker alive
                traceback.print_exc()
                status, extra = "error", {"error": f"{type(e).__name__}: {e}"}
            self._unmark(key)
            self._status(st, status, finished_at=time.time(), **extra)

    # ------------------------------------------------------------------ interrupted jobs
    def _mark(self, job: Job) -> None:
        """Write into meta.json that this process is working on the job. A job that already has a
        translation keeps it (and the edits made to it) even if this run is interrupted."""
        has_result = "translate" in job.reload().get("stages", {}) or job.path("segments.ko.json").exists()
        job.update_meta(running={"pid": os.getpid(), "proc_started": _proc_started(os.getpid()), "keep": has_result})
        self.marked[job.key] = job

    def _unmark(self, key: str) -> None:
        job = self.marked.pop(key, None)
        if job is None or self.closing or not job.meta_path.exists():
            return
        try:
            job.update_meta(lambda m: m.pop("running", None))
        except OSError:
            traceback.print_exc()

    def close(self) -> None:
        """The app is exiting. Whatever is queued or running stays marked (also when it now fails
        because its llama-server was stopped), so the next start deletes it."""
        self.closing = True

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
        self._mark(job)

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
                          "cloud": t.get("mode") not in ("managed", "external", None)}
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


TEMP_FILES = ("*.tmp", "*.tmp.wav", "*.f32")  # write_json_atomic, FFmpeg output, vocal separation


def _proc_started(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def _owner_alive(mark: dict[str, Any]) -> bool:
    """Is the process that marked the job still running? (The pid alone can be a reused one.)"""
    pid, started = mark.get("pid"), mark.get("proc_started")
    if not isinstance(pid, int) or not isinstance(started, (int, float)):
        return False
    now = _proc_started(pid)
    return now is not None and abs(now - started) < 1.0


def _remove_job_dir(d: Path) -> None:
    for p in d.iterdir():  # meta.json goes last: a folder that could not be emptied is tried again next time
        if p.name == "meta.json":
            continue
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    shutil.rmtree(d)


def purge_interrupted(busy: Callable[[str], bool] = lambda key: False) -> list[str]:
    """Delete the jobs an earlier run of the app left half done. Called when the server starts.

    A job is deleted whole (audio, transcript, everything in its folder), not resumed. Two kinds are
    kept and only lose their temporary files: jobs that had a translation before the interrupted run
    (a rerun; the subtitles may carry edits), and jobs whose translation finished in that run.
    Jobs of another backend process that is still alive are left alone."""
    gone: list[str] = []
    if not paths.jobs.exists():
        return gone
    for d in list(paths.jobs.iterdir()):
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mark = meta.get("running") if isinstance(meta, dict) else None
        if not isinstance(mark, dict):
            continue
        if _owner_alive(mark) and (mark.get("pid") != os.getpid() or busy(d.name)):
            continue
        try:
            if mark.get("keep") or "translate" in meta.get("stages", {}):
                for pattern in TEMP_FILES:
                    for f in d.glob(pattern):
                        f.unlink(missing_ok=True)
                with _lock_for(d / "meta.json"):
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                    meta.pop("running", None)
                    write_json_atomic(d / "meta.json", meta)
            else:
                _remove_job_dir(d)
                gone.append(d.name)
        except OSError as e:
            print(f"could not clean up the interrupted job {d.name}: {type(e).__name__}: {e}", file=sys.stderr)
    return gone


def purge_job(key: str) -> bool:
    d = paths.jobs / key
    if d.exists() and d.parent == paths.jobs:
        shutil.rmtree(d)
        return True
    return False
