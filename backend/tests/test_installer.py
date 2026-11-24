"""Download helper: resume, server ignoring Range, hash mismatch, cancel."""

from __future__ import annotations

import hashlib
import threading

import httpx
import pytest

import jamak.installer as ins

DATA = bytes(range(256)) * 4096  # 1 MiB
SHA = hashlib.sha256(DATA).hexdigest()


def serve(monkeypatch, honor_range=True, body=DATA, seen=None):
    def handler(req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("range")
        if seen is not None:
            seen.append(rng)
        if rng and honor_range:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    monkeypatch.setattr(ins, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def noop(*_):
    pass


def test_fresh_download(tmp_path, monkeypatch):
    serve(monkeypatch)
    got = []
    out = ins.download("http://x/f.bin", tmp_path / "f.bin", SHA, len(DATA), lambda d, t, m: got.append(d),
                       threading.Event())
    assert out.read_bytes() == DATA and got[-1] == len(DATA)
    assert not (tmp_path / "f.bin.part").exists()


def test_resume_from_part(tmp_path, monkeypatch):
    seen = []
    serve(monkeypatch, seen=seen)
    (tmp_path / "f.bin.part").write_bytes(DATA[:300_000])
    ins.download("http://x/f.bin", tmp_path / "f.bin", SHA, len(DATA), noop, threading.Event())
    assert seen == ["bytes=300000-"]
    assert (tmp_path / "f.bin").read_bytes() == DATA


def test_server_ignores_range(tmp_path, monkeypatch):
    serve(monkeypatch, honor_range=False)
    (tmp_path / "f.bin.part").write_bytes(b"garbage" * 1000)
    ins.download("http://x/f.bin", tmp_path / "f.bin", SHA, len(DATA), noop, threading.Event())
    assert (tmp_path / "f.bin").read_bytes() == DATA


def test_hash_mismatch_removes_part(tmp_path, monkeypatch):
    serve(monkeypatch, body=DATA[:-1] + b"\x00")
    with pytest.raises(ins.InstallError, match="SHA-256"):
        ins.download("http://x/f.bin", tmp_path / "f.bin", SHA, len(DATA), noop, threading.Event())
    assert not (tmp_path / "f.bin").exists() and not (tmp_path / "f.bin.part").exists()


def test_cancel_keeps_part_for_resume(tmp_path, monkeypatch):
    serve(monkeypatch)
    ev = threading.Event()
    ev.set()
    with pytest.raises(ins.Cancelled):
        ins.download("http://x/f.bin", tmp_path / "f.bin", SHA, len(DATA), noop, ev)
    assert not (tmp_path / "f.bin").exists()


def test_existing_verified_file_is_kept(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not download")

    monkeypatch.setattr(ins, "_client", boom)
    (tmp_path / "f.bin").write_bytes(DATA)
    ins.download("http://x/f.bin", tmp_path / "f.bin", SHA, len(DATA), noop, threading.Event())


def test_setup_status_lists_components(jamak_home):
    m = ins.SetupManager()
    ids = [s["id"] for s in m.status()]
    assert ids == ["ffmpeg", "cuda", "llama", "whisper", "llm"]
    assert all(s["status"] in ("present", "missing") for s in m.status())
