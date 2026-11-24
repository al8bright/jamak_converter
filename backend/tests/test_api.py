"""API tests with the pipeline and translation replaced by fakes (no GPU)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import jamak.api.jobs as jobs_mod
from jamak.core.cache import Job, write_json_atomic
from jamak.core.pipeline import RunResult
from jamak.core.types import Line, Lines


class FakePipeline:
    def __init__(self, video, opts, on_event=None, cancel=None, jobs_root=None):
        self.video = Path(video)
        self.opts = opts
        self.on_event = on_event or (lambda e: None)
        self.cancel = cancel
        self.job = Job(self.video, jobs_root)

    def run(self, force_from=None):
        for i, stage in enumerate(["audio", "stt", "segment", "translate", "export"]):
            if self.cancel is not None and self.cancel.is_set():
                raise jobs_mod.Cancelled()
            self.on_event({"job": self.job.key, "stage": stage, "status": "done", "progress": 1.0,
                           "overall": (i + 1) / 5, "message": ""})
            time.sleep(0.01)
        lines = [Line(id=1, start=1, end=2, ja="おはよう", ko="안녕"),
                 Line(id=2, start=3, end=4, ja="うん", ko="응")]
        write_json_atomic(self.job.path("segments.ja.json"), Lines(lines=[ln.model_copy(update={"ko": ""}) for ln in lines]))
        write_json_atomic(self.job.path("segments.ko.json"), Lines(lines=lines))
        self.job.mark_done("translate", "fp", 0.1)
        return RunResult(job_key=self.job.key, job_dir=self.job.dir, outputs=[], timings={"stt": 0.1})


def fake_translation(lines, opts, job, progress=None, cancel=None, only_ids=None):
    for ln in lines:
        if only_ids is None or ln.id in only_ids:
            ln.ko = f"재번역{ln.id}"
    return {"requests": 1, "failed_lines": 0, "completion_tokens": 1}


@pytest.fixture
def client(jamak_home, monkeypatch):
    monkeypatch.setattr(jobs_mod, "Pipeline", FakePipeline)
    monkeypatch.setattr(jobs_mod, "run_translation", fake_translation)
    monkeypatch.setattr("jamak.models.require_stt", lambda model_id: None)  # no models in the test home
    from jamak.api.server import create_app

    with TestClient(create_app(token="")) as c:
        yield c


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "ep01.mkv"
    v.write_bytes(b"fake video bytes" * 100)
    return v


def wait_done(client, key, timeout=5.0):
    t = time.time()
    while time.time() - t < timeout:
        live = client.get(f"/jobs/{key}").json()["live"]
        if live and live["status"] in ("done", "error", "cancelled"):
            return live
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_health_and_host_guard(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 403


def test_token_required_when_configured(jamak_home):
    from jamak.api.server import create_app

    with TestClient(create_app(token="s3cret")) as c:
        assert c.get("/health").status_code == 200  # health stays open for the launcher
        assert c.get("/jobs").status_code == 401
        assert c.get("/jobs", headers={"x-jamak-token": "s3cret"}).status_code == 200


def test_job_lifecycle_edit_retranslate_export(client, video, tmp_path):
    r = client.post("/jobs", json={"video_path": str(video)})
    assert r.status_code == 202
    key = r.json()["key"]
    live = wait_done(client, key)
    assert live["status"] == "done" and live["progress"] == 1.0

    listed = client.get("/jobs").json()
    assert listed[0]["key"] == key and listed[0]["translated"]

    seg = client.get(f"/jobs/{key}/segments").json()
    assert seg["source"] == "ko" and [ln["ko"] for ln in seg["lines"]] == ["안녕", "응"]

    r = client.patch(f"/jobs/{key}/segments", json={"edits": [{"id": 2, "ko": "응, 그래"}]})
    assert r.status_code == 200
    line2 = client.get(f"/jobs/{key}/segments").json()["lines"][1]
    assert line2["ko"] == "응, 그래" and line2["locked"] is True

    r = client.post(f"/jobs/{key}/retranslate", json={"ids": [1, 2]})
    assert r.status_code == 202
    wait_done(client, key)
    lines = client.get(f"/jobs/{key}/segments").json()["lines"]
    assert [ln["ko"] for ln in lines] == ["재번역1", "재번역2"]  # explicit retranslate unlocks

    out_dir = tmp_path / "subs"
    r = client.post(f"/jobs/{key}/export", json={"output": {"formats": ["srt"], "out_dir": str(out_dir)}})
    assert r.status_code == 200
    srt = (out_dir / "ep01.ko.srt").read_text(encoding="utf-8")
    assert "재번역1" in srt


def test_bad_inputs(client, video):
    assert client.post("/jobs", json={"video_path": "C:/nope/missing.mkv"}).status_code == 404
    assert client.get("/jobs/zzzz").status_code == 400
    assert client.get("/jobs/0123456789abcdef").status_code == 404
    r = client.post("/jobs", json={"video_path": str(video)})
    key = r.json()["key"]
    wait_done(client, key)
    bad = client.patch(f"/jobs/{key}/segments", json={"edits": [{"id": 1, "start": 5, "end": 4}]})
    assert bad.status_code == 422


def test_websocket_streams_progress(client, video):
    r = client.post("/jobs", json={"video_path": str(video)})
    key = r.json()["key"]
    wait_done(client, key)
    # a new run, observed over the socket
    with client.websocket_connect(f"/jobs/{key}/events") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        client.post("/jobs", json={"video_path": str(video)})
        seen = []
        while True:
            e = ws.receive_json()
            seen.append(e)
            if e.get("type") == "status" and e.get("status") in ("done", "error"):
                break
    assert any(e.get("type") == "progress" and e["stage"] == "stt" for e in seen)
    assert seen[-1]["status"] == "done"


def test_glossary_crud(client):
    assert client.get("/glossaries").json() == []
    r = client.put("/glossaries/my show", json={"entries": [{"ja": "ユウキ", "ko": "유키"}]})
    assert r.status_code == 200
    assert [g["name"] for g in client.get("/glossaries").json()] == ["my show"]
    assert client.get("/glossaries/my show").json()["entries"][0]["ko"] == "유키"
    assert client.put("/glossaries/..%2Fevil", json={"entries": []}).status_code in (400, 404)
    client.delete("/glossaries/my show")
    assert client.get("/glossaries").json() == []


def test_job_needs_downloaded_model(jamak_home, monkeypatch, video):
    monkeypatch.setattr(jobs_mod, "Pipeline", FakePipeline)
    from jamak.api.server import create_app

    with TestClient(create_app(token="")) as c:
        r = c.post("/jobs", json={"video_path": str(video), "options": {"stt": {"model": "qwen3-asr-1.7b"}}})
        assert r.status_code == 409 and "모델 관리" in r.json()["detail"]
        setup = c.get("/setup").json()
        assert "ready" in setup and "recommended_stt" in setup and setup["free_bytes"] > 0
