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
    from jamak import models

    m = ins.SetupManager()
    st = m.status()
    assert [s["id"] for s in st] == [s.id for s in models.SPECS]
    assert {"large-v3", "large-v3-turbo", "kotoba-whisper-v2.0", "qwen3-asr-1.7b", "llm"} <= {s["id"] for s in st}
    assert all(s["status"] in ("present", "missing") for s in st)
    qwen = next(s for s in st if s["id"] == "qwen3-asr-1.7b")
    assert qwen["requires"] == ["torch"] and qwen["missing_requires"] == ["torch"]


def test_dependencies_follow_gpu():
    assert ins.with_dependencies(["large-v3"], gpu=True) == ["cuda", "large-v3"]
    assert ins.with_dependencies(["large-v3"], gpu=False) == ["large-v3"]
    assert ins.with_dependencies(["qwen3-asr-1.7b", "llm"], gpu=True) == ["torch", "llama", "qwen3-asr-1.7b", "llm"]


def test_recommended_stt_by_vram():
    assert ins.recommended_stt(24) == "large-v3"
    assert ins.recommended_stt(6) == "large-v3-turbo"
    assert ins.recommended_stt(None) == "large-v3-turbo"


def test_install_rejects_unknown(jamak_home):
    with pytest.raises(ValueError):
        ins.SetupManager().install(["nope"])


def test_remove_protects_dependencies_and_ffmpeg(jamak_home, monkeypatch):
    m = ins.SetupManager()
    with pytest.raises(ValueError, match="지울 수 없"):
        m.remove("ffmpeg")
    monkeypatch.setattr(ins, "has_nvidia_gpu", lambda: True)
    monkeypatch.setattr(ins.HfModelComponent, "present", lambda self: self.id == "large-v3")
    with pytest.raises(ValueError, match="Whisper large-v3"):
        m.remove("cuda")


def fake_model(model_id):
    from jamak import models

    s = models.BY_ID[model_id]
    snap = models.repo_dir(models.hf_cache_dir(s), s.repos[0]) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    return snap


def test_remove_deletes_model_folder(jamak_home):
    snap = fake_model("large-v3-turbo")
    assert ins.present("large-v3-turbo")
    with pytest.raises(ValueError, match="하나는 있어야"):  # the only speech-recognition model
        ins.SetupManager().remove("large-v3-turbo")
    fake_model("large-v3")
    ins.SetupManager().remove("large-v3-turbo")
    assert not ins.present("large-v3-turbo") and not snap.exists()


def test_kotoba_alignment_heads_fixed(tmp_path):
    import json

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"alignment_heads": [[25, 3]], "lang_ids": [1]}), encoding="utf-8")
    assert ins.fix_alignment_heads("kotoba-whisper-v2.0", tmp_path)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["alignment_heads"] == [[1, h] for h in range(20)] and data["lang_ids"] == [1]
    assert not ins.fix_alignment_heads("kotoba-whisper-v2.0", tmp_path)  # already fixed
    assert not ins.fix_alignment_heads("large-v3", tmp_path)


def test_runtime_files_are_packaged():
    lock = ins.runtime_file("qwen-requirements.lock").read_text(encoding="utf-8")
    assert "qwen-asr==0.0.6" in lock and "torch==" in lock and "--hash=sha256:" in lock
    pkgs = [ln.split("==")[0] for ln in lock.splitlines() if "==" in ln and not ln.startswith("#")]
    assert "gradio" not in pkgs and "vllm" not in pkgs
    assert ins.runtime_file("qwen_asr_worker.py").exists()


def test_translation_models_are_separate_files(jamak_home, monkeypatch):
    """Each GGUF in the list is its own item: present/remove look at its own file only, and local
    translation is ready with any of them (plus llama-server)."""
    from jamak import models
    from jamak.config import paths

    llms = [s for s in models.SPECS if s.kind == "llm"]
    assert len(llms) >= 2 and len({s.gguf[1] for s in llms}) == len(llms)
    assert all(s.gguf[0] and s.gguf[1].endswith(".gguf") and s.requires == ("llama",) for s in llms)
    assert ins.with_dependencies(["qwen3.5-9b"], gpu=True) == ["llama", "qwen3.5-9b"]

    monkeypatch.setattr(ins.LlamaComponent, "present", lambda self: True)
    paths.models.mkdir(parents=True, exist_ok=True)
    (paths.models / models.BY_ID["qwen3.5-9b"].gguf[1]).write_bytes(b"x")
    assert ins.present("qwen3.5-9b") and not ins.present("llm")
    assert ins.SetupManager().ready()["local_translation"]
    ins.SetupManager().remove("qwen3.5-9b")
    assert not ins.present("qwen3.5-9b") and not ins.SetupManager().ready()["local_translation"]
