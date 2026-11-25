"""A job the app was closed in the middle of is deleted at the next start, not resumed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

import jamak.api.jobs as jobs_mod
from jamak.core.cache import Job, write_json_atomic
from jamak.core.pipeline import RunResult
from jamak.core.types import Line, Lines

DEAD = {"pid": 2**22 + 12345, "proc_started": 1.0, "keep": False}  # a process that is not there


class SlowPipeline:
    """Finishes the audio stage, then waits for `go`. `fail` makes it raise instead of translating."""

    go = threading.Event()
    at_stt = threading.Event()
    fail = False

    def __init__(self, video, opts, on_event=None, cancel=None, jobs_root=None):
        self.cancel = cancel
        self.job = Job(Path(video), jobs_root)

    def run(self, force_from=None):
        self.job.path("audio.wav").write_bytes(b"RIFF")
        self.job.mark_done("audio", "fp", 0.1)
        self.job.path("audio.tmp.wav").write_bytes(b"half")
        SlowPipeline.at_stt.set()
        assert SlowPipeline.go.wait(10)
        if self.cancel.is_set():
            raise jobs_mod.Cancelled()
        if SlowPipeline.fail:
            raise ConnectionError("llama-server is gone")
        write_json_atomic(self.job.path("segments.ko.json"), Lines(lines=[Line(id=1, start=1, end=2, ja="はい", ko="네")]))
        self.job.mark_done("translate", "fp", 0.1)
        return RunResult(job_key=self.job.key, job_dir=self.job.dir, outputs=[])


@pytest.fixture
def app(jamak_home, monkeypatch):
    SlowPipeline.go, SlowPipeline.at_stt, SlowPipeline.fail = threading.Event(), threading.Event(), False
    monkeypatch.setattr(jobs_mod, "Pipeline", SlowPipeline)
    monkeypatch.setattr("jamak.models.require_stt", lambda model_id: None)
    from jamak.api.server import create_app

    yield lambda: TestClient(create_app(token=""))
    SlowPipeline.go.set()  # never leave a worker thread waiting


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "movie.mkv"
    v.write_bytes(b"fake video bytes" * 100)
    return v


def meta_of(job_dir: Path) -> dict:
    return json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))


def wait_status(c, key, *want, timeout=5.0):
    t = time.time()
    while time.time() - t < timeout:
        live = c.get(f"/jobs/{key}").json()["live"]
        if live and live["status"] in want:
            return live
        time.sleep(0.02)
    raise AssertionError(f"job did not reach {want}")


def half_done_job(video: Path, mark: dict | None, translated: bool = False) -> Job:
    """What a killed backend leaves behind."""
    job = Job(video)
    job.path("audio.wav").write_bytes(b"RIFF")
    job.mark_done("audio", "fp", 0.1)
    for name in ("audio.tmp.wav", "tmpabc.f32", "meta.json.1.2.tmp"):
        job.path(name).write_bytes(b"half")
    (job.dir / "sub").mkdir()
    (job.dir / "sub" / "x.bin").write_bytes(b"x")
    if translated:
        write_json_atomic(job.path("segments.ko.json"), Lines(lines=[Line(id=1, start=1, end=2, ja="はい", ko="네 (고침)")]))
        job.mark_done("translate", "fp", 0.1)
    if mark is not None:
        job.update_meta(running=mark)
    return job


def test_mark_is_there_only_while_the_job_runs(app, video):
    with app() as c:
        key = c.post("/jobs", json={"video_path": str(video)}).json()["key"]
        assert SlowPipeline.at_stt.wait(5)
        job_dir = Job(video).dir
        mark = meta_of(job_dir)["running"]
        assert mark["pid"] == os.getpid() and mark["keep"] is False
        assert abs(mark["proc_started"] - psutil.Process().create_time()) < 1
        assert jobs_mod.purge_interrupted(c.app.state.manager.busy) == []  # our own running job is not touched
        SlowPipeline.go.set()
        assert wait_status(c, key, "done", "error")["status"] == "done"
        assert "running" not in meta_of(job_dir)


@pytest.mark.parametrize("how", ["cancel", "error"])
def test_cancel_and_error_keep_the_job(app, video, how):
    """Only closing the app deletes a job: after a cancel or an error the finished stages stay."""
    SlowPipeline.fail = how == "error"
    with app() as c:
        key = c.post("/jobs", json={"video_path": str(video)}).json()["key"]
        assert SlowPipeline.at_stt.wait(5)
        if how == "cancel":
            c.post(f"/jobs/{key}/cancel")
        SlowPipeline.go.set()
        wait_status(c, key, "cancelled", "error")
        assert "running" not in meta_of(Job(video).dir)
    with app() as c:
        assert [j["key"] for j in c.get("/jobs").json()] == [key]


def test_interrupted_job_is_deleted_at_the_next_start(app, video):
    job = half_done_job(video, DEAD)
    with app() as c:
        assert c.get("/jobs").json() == []
    assert not job.dir.exists()
    assert video.exists()  # only the job folder goes, never the video


def test_job_closed_in_the_middle_is_gone_after_restart(app, video):
    """The whole path: the server shuts down while a job runs (and that job then fails or even
    finishes its current step); the next server deletes it."""
    SlowPipeline.fail = True
    with app() as c:
        c.post("/jobs", json={"video_path": str(video)})
        assert SlowPipeline.at_stt.wait(5)
        mgr = c.app.state.manager
    assert mgr.closing
    SlowPipeline.go.set()  # the worker now fails, as it does when llama-server is stopped under it
    t = time.time()
    while mgr.states and next(iter(mgr.states.values())).status == "running" and time.time() - t < 5:
        time.sleep(0.02)
    job_dir = Job(video).dir
    assert meta_of(job_dir)["running"]["pid"] == os.getpid()  # still marked
    with app() as c:
        assert c.get("/jobs").json() == []
    assert not job_dir.exists()


def test_rerun_of_a_translated_job_keeps_the_subtitles(app, video):
    job = half_done_job(video, {**DEAD, "keep": True}, translated=True)
    with app() as c:
        listed = c.get("/jobs").json()
        assert [j["key"] for j in listed] == [job.key] and listed[0]["translated"]
        assert c.get(f"/jobs/{job.key}/segments").json()["lines"][0]["ko"] == "네 (고침)"
    assert "running" not in meta_of(job.dir)
    assert sorted(p.name for p in job.dir.iterdir()) == ["audio.wav", "meta.json", "segments.ko.json", "sub"]


def test_translation_that_finished_before_the_kill_is_kept(app, video):
    job = half_done_job(video, DEAD, translated=True)  # killed between the last stage and the unmark
    with app():
        pass
    assert job.path("segments.ko.json").exists() and "running" not in meta_of(job.dir)


def test_job_of_another_live_backend_is_left_alone(app, video):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        mark = {"pid": other.pid, "proc_started": psutil.Process(other.pid).create_time(), "keep": False}
        job = half_done_job(video, mark)
        with app():
            pass
        assert job.dir.exists() and "running" in meta_of(job.dir)
        # same pid, another start time: the pid was reused by an unrelated process
        job.update_meta(running={**mark, "proc_started": mark["proc_started"] - 100})
        with app():
            pass
        assert not job.dir.exists()
    finally:
        other.kill()
        other.wait()


def test_unmarked_and_broken_folders_are_not_touched(app, video, jamak_home):
    job = half_done_job(video, None)  # e.g. cancelled earlier, or made by `jamak run`
    from jamak.config import paths

    broken = paths.jobs / "0000000000000000"
    broken.mkdir(parents=True)
    (broken / "meta.json").write_text("{not json", encoding="utf-8")
    with app() as c:
        assert [j["key"] for j in c.get("/jobs").json()] == [job.key]
    assert job.path("audio.tmp.wav").exists() and broken.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only: on Windows a Job Object stops the children")
def test_sigterm_stops_the_children(tmp_path):
    """macOS: the app asks the backend to stop with SIGTERM. llama-server must be gone right away, even
    while a worker thread is still busy (Python would wait for it before running atexit)."""
    import signal

    import httpx

    code = (
        "import subprocess, sys, threading, time\n"
        "from jamak.core import proc\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "proc.bind_to_lifetime(child)\n"
        "print('CHILD', child.pid, flush=True)\n"
        "threading.Thread(target=time.sleep, args=(120,)).start()\n"
        "from jamak.api.server import serve\n"
        "serve(port=0)\n"
    )
    env = {**os.environ, "JAMAK_HOME": str(tmp_path / "home"), "JAMAK_TOKEN": "t", "PYTHONUNBUFFERED": "1"}
    server = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)
    child = None
    try:
        child = int(server.stdout.readline().split()[1])
        url = server.stdout.readline().split()[1]
        t = time.time()
        while time.time() - t < 20:  # uvicorn takes over SIGTERM only once it is serving
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert psutil.Process(child).is_running()

        def alive() -> bool:
            try:
                return psutil.Process(child).status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return False

        server.send_signal(signal.SIGTERM)
        t = time.time()
        while alive() and time.time() - t < 3:  # 3 s is how long the app waits before SIGKILL
            time.sleep(0.05)
        assert not alive(), "the child outlived SIGTERM"
    finally:
        server.kill()
        server.wait()
        if child and psutil.pid_exists(child):
            try:
                psutil.Process(child).kill()
            except psutil.Error:
                pass
