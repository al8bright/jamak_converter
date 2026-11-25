"""Mac (Apple Silicon) code paths, run on any OS: the platform is patched and mlx-whisper / the Keychain
are replaced by small fakes. A real Mac run is still needed (TODO.md 4장)."""

from __future__ import annotations

import io
import sys
import tarfile
import threading
import types
import wave
import zipfile

import numpy as np
import pytest

import jamak.installer as ins
from jamak import hw, models
from jamak.config import SttOptions, paths


def test_mac_model_list():
    mac = {s.id: s for s in models.specs_for("mac")}
    win = {s.id: s for s in models.specs_for("win")}
    assert not {"cuda", "torch", "qwen3-asr-1.7b"} & mac.keys()
    for i in ("large-v3", "large-v3-turbo", "kotoba-whisper-v2.0"):
        assert mac[i].engine == "mlx-whisper" and mac[i].gpu_requires == () and mac[i].repos != win[i].repos
        assert win[i].engine == "faster-whisper"
    assert mac["llama"].note.endswith("Metal") and win["llama"].note.endswith("12.4")
    assert {s.id for s in models.specs_for("mac") if s.kind == "llm"} == {s.id for s in win.values() if s.kind == "llm"}
    # every id appears once per platform
    for p in ("win", "mac"):
        ids = [s.id for s in models.specs_for(p)]
        assert len(ids) == len(set(ids))


def test_host_uses_its_own_list():
    from tests.conftest import HOST_SPECS

    assert HOST_SPECS == models.specs_for("mac" if sys.platform == "darwin" else "win")  # hw.IS_MAC is patched here
    engines = {s.engine for s in HOST_SPECS if s.kind == "stt"}
    assert engines == ({"mlx-whisper"} if sys.platform == "darwin" else {"faster-whisper", "qwen-asr"})


def test_stt_identity_includes_engine(monkeypatch):
    from jamak.core.pipeline import PipelineOptions, stt_identity

    o = PipelineOptions()
    assert stt_identity(o)["engine"] == "faster-whisper"
    mac = {s.id: s for s in models.specs_for("mac")}
    monkeypatch.setattr(models, "BY_ID", mac)
    d = stt_identity(o)
    assert d["engine"] == "mlx-whisper"  # Windows and Mac transcripts never share a cache entry
    assert "batch_size" not in d


def test_mac_data_folder(monkeypatch, tmp_path):
    from jamak import config

    monkeypatch.delenv("JAMAK_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    assert config._default_home() == tmp_path / "Library" / "Application Support" / "jamak_converter"


def test_apple_gpu_uses_share_of_unified_memory(monkeypatch):
    monkeypatch.setattr(hw, "IS_MAC", True)
    monkeypatch.setattr(hw.platform, "machine", lambda: "arm64")
    hw.apple_chip.cache_clear()
    monkeypatch.setattr(hw, "_run", lambda args, timeout=5: {"hw.memsize": str(32 * 2**30),
                                                             "machdep.cpu.brand_string": "Apple M2 Pro"}[args[-1]])
    try:
        g = hw.gpus()
        assert g[0]["name"] == "Apple M2 Pro" and g[0]["unified"] and g[0]["ram_total_mb"] == 32 * 1024
        assert hw.model_memory_gb() == pytest.approx(32 * hw.METAL_SHARE, abs=0.1)
        assert hw.accelerator() == "metal"
    finally:
        hw.apple_chip.cache_clear()


# --- Keychain ---------------------------------------------------------------------------------------

class FakeKeychain:
    items: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, value):
        self.items[(service, user)] = value

    def get_password(self, service, user):
        return self.items.get((service, user))

    def delete_password(self, service, user):
        del self.items[(service, user)]


@pytest.fixture
def keychain(monkeypatch):
    from jamak.core import secrets

    FakeKeychain.items = {}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(secrets, "_keychain", FakeKeychain)
    return FakeKeychain.items


def test_keychain_keeps_only_a_reference_in_the_db(keychain):
    from jamak.core import secrets

    blob = secrets.protect("sk-ant-test-0123456789abcdefWXYZ")
    assert blob.startswith(b"keychain:") and b"sk-ant" not in blob
    assert secrets.unprotect(blob) == "sk-ant-test-0123456789abcdefWXYZ"
    secrets.forget(blob)
    assert keychain == {}
    with pytest.raises(secrets.SecretError, match="다시 등록"):
        secrets.unprotect(blob)


def test_keychain_error_hides_the_key(monkeypatch, keychain):
    from jamak.core import secrets

    class Broken(FakeKeychain):
        def set_password(self, *_):
            raise RuntimeError("denied: sk-ant-test-0123456789abcdefWXYZ")

    monkeypatch.setattr(secrets, "_keychain", Broken)
    with pytest.raises(secrets.SecretError) as e:
        secrets.protect("sk-ant-test-0123456789abcdefWXYZ")
    assert "sk-ant" not in str(e.value) and e.value.__cause__ is None


def test_removing_a_key_deletes_the_keychain_item(jamak_home, keychain):
    from jamak.core.store import Store

    jamak_home.mkdir(parents=True, exist_ok=True)
    db = Store(jamak_home / "t.db")
    k = db.add_key("anthropic", "test", "sk-ant-test-0123456789abcdefWXYZ")
    assert len(keychain) == 1
    assert db.key_secret(k["id"]) == "sk-ant-test-0123456789abcdefWXYZ"
    assert db.remove_key(k["id"])
    assert keychain == {}


def test_windows_key_on_a_mac_asks_to_register_again(monkeypatch):
    from jamak.core import secrets

    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(secrets.SecretError, match="다시 등록"):
        secrets.unprotect(b"\x01\x00\x00\x00dpapi-blob")


# --- mlx-whisper ------------------------------------------------------------------------------------

def _wav(path, seconds=4.0):
    t = np.arange(int(16000 * seconds)) / 16000
    pcm = (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def fake_mlx(monkeypatch, tmp_path):
    """mlx_whisper.transcribe with the same tqdm loop and result shape as mlx-whisper 0.4.3."""
    calls = {}
    real_tqdm = types.SimpleNamespace(tqdm=None)
    mod = types.ModuleType("mlx_whisper.transcribe")
    mod.tqdm = real_tqdm

    class Encoder(dict):  # like mlx's nn.Module: a dict of its parts that can be called
        runs = 0

        def __call__(self, mel):
            Encoder.runs += 1
            return mel * 2

    class Model(dict):
        def __init__(self):
            self["encoder"] = Encoder()

        def __getattr__(self, key):
            try:
                return self[key]
            except KeyError:
                raise AttributeError(key) from None

    class ModelHolder:
        model = Model()

        @classmethod
        def get_model(cls, path, dtype):
            return cls.model

    mod.ModelHolder = ModelHolder
    mod.Encoder = Encoder
    core = types.ModuleType("mlx.core")
    core.float16 = "float16"
    core.array_equal = lambda a, b: np.array(np.array_equal(a, b))
    core.clear_cache = lambda: None
    mlx = types.ModuleType("mlx")
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)

    def transcribe(audio, *, path_or_hf_repo, **kw):
        calls.update(kw, path=path_or_hf_repo, n=len(audio))
        frames = len(audio) // 160
        model = ModelHolder.get_model(path_or_hf_repo, None)
        with mod.tqdm.tqdm(total=frames, unit="frames", disable=kw.get("verbose") is not False) as bar:
            for i in range(4):
                mel = np.full((1, 30, 8), float(i), dtype=np.float32)
                first = model.encoder(mel)  # decoding
                again = model.encoder(mel.copy())  # a temperature retry / word timestamps: same window
                assert np.array_equal(first, again) and np.array_equal(first, mel * 2)
                bar.update(frames // 4)
        return {"text": "おはよう", "language": "ja", "segments": [
            {"start": 0.5, "end": 1.6, "text": " おはよう", "avg_logprob": -0.2, "no_speech_prob": 0.01,
             "compression_ratio": 1.1, "words": [
                 {"word": "おは", "start": 0.5, "end": 1.0, "probability": 0.9},
                 {"word": "よう", "start": 1.0, "end": 1.6, "probability": 0.8}]},
            {"start": 2.0, "end": 2.2, "text": " ", "words": []},
        ]}

    mod.transcribe = transcribe
    pkg = types.ModuleType("mlx_whisper")
    pkg.transcribe = mod
    monkeypatch.setitem(sys.modules, "mlx_whisper", pkg)
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", mod)
    model = tmp_path / "model"
    model.mkdir()
    (model / "weights.safetensors").write_bytes(b"x")
    return calls, model, mod, real_tqdm


def test_mlx_recognizer_maps_result(fake_mlx, tmp_path):
    from jamak.core.stt.mlx_whisper import MlxWhisperRecognizer

    calls, model, mod, real_tqdm = fake_mlx
    seen = []
    r = MlxWhisperRecognizer(SttOptions(model=str(model), vad=False))
    t = r.transcribe(_wav(tmp_path / "a.wav"), "登場人物：しんのすけ。", lambda f, m: seen.append(f))
    assert calls["path"] == str(model) and calls["language"] == "ja" and calls["word_timestamps"]
    assert calls["condition_on_previous_text"] is False and calls["initial_prompt"] == "登場人物：しんのすけ。"
    assert "beam_size" not in calls  # mlx-whisper has no beam search
    assert calls["clip_timestamps"] == "0"
    assert len(t.segments) == 1 and t.segments[0].text == "おはよう" and t.duration == pytest.approx(4.0)
    assert [w.text for w in t.segments[0].words] == ["おは", "よう"] and t.segments[0].words[1].prob == 0.8
    assert seen[-1] == pytest.approx(0.999, abs=0.01) and seen == sorted(seen)
    assert mod.tqdm is real_tqdm  # restored


def test_mlx_encoder_runs_once_per_window(fake_mlx, tmp_path):
    from jamak.core.stt.mlx_whisper import MlxWhisperRecognizer

    _calls, model, mod, _ = fake_mlx
    real = mod.ModelHolder.model.encoder
    r = MlxWhisperRecognizer(SttOptions(model=str(model), vad=False))
    r.transcribe(_wav(tmp_path / "a.wav"), None)
    assert mod.Encoder.runs == 4  # 4 windows, each asked for twice
    assert (r.encoder_calls, r.encoder_reused) == (8, 4)
    held = mod.ModelHolder.model
    assert held.encoder is real and held["encoder"] is real and "encoder" not in held.__dict__  # put back


def test_mlx_encoder_restored_after_cancel(fake_mlx, tmp_path):
    from jamak.core.stt import mlx_whisper as mw

    _calls, model, mod, _ = fake_mlx
    real = mod.ModelHolder.model.encoder
    stop = threading.Event()
    stop.set()
    with pytest.raises(mw.Cancelled):
        mw.MlxWhisperRecognizer(SttOptions(model=str(model), vad=False)).transcribe(
            _wav(tmp_path / "a.wav"), None, cancel=stop)
    assert mod.ModelHolder.model.encoder is real


def test_mlx_recognizer_passes_vad_spans(fake_mlx, tmp_path, monkeypatch):
    from jamak.core.stt import mlx_whisper as mw
    from jamak.core.stt import qwen_asr

    calls, model, _mod, _ = fake_mlx
    monkeypatch.setattr(qwen_asr, "speech_chunks", lambda samples, opts: [(0.4, 1.8), (2.5, 3.9)])
    mw.MlxWhisperRecognizer(SttOptions(model=str(model), vad=True)).transcribe(_wav(tmp_path / "a.wav"), None)
    assert calls["clip_timestamps"] == [0.4, 1.8, 2.5, 3.9] and calls["initial_prompt"] is None


def test_mlx_recognizer_skips_silent_audio(fake_mlx, tmp_path, monkeypatch):
    from jamak.core.stt import mlx_whisper as mw
    from jamak.core.stt import qwen_asr

    calls, model, _mod, _ = fake_mlx
    monkeypatch.setattr(qwen_asr, "speech_chunks", lambda samples, opts: [])
    t = mw.MlxWhisperRecognizer(SttOptions(model=str(model), vad=True)).transcribe(_wav(tmp_path / "a.wav"), None)
    assert t.segments == [] and calls == {}


def test_mlx_recognizer_cancel(fake_mlx, tmp_path):
    from jamak.core.stt import mlx_whisper as mw

    _calls, model, mod, real_tqdm = fake_mlx
    stop = threading.Event()
    stop.set()
    with pytest.raises(mw.Cancelled):
        mw.MlxWhisperRecognizer(SttOptions(model=str(model), vad=False)).transcribe(
            _wav(tmp_path / "a.wav"), None, cancel=stop)
    assert type(mw.Cancelled()).__name__ == "Cancelled"  # the pipeline matches on this name
    assert mod.tqdm is real_tqdm


def test_mlx_model_must_be_downloaded(jamak_home, monkeypatch):
    from jamak.core.stt.mlx_whisper import model_dir

    monkeypatch.setattr(models, "BY_ID", {s.id: s for s in models.specs_for("mac")})
    with pytest.raises(models.ModelMissing, match="받지 않았습니다"):
        model_dir("large-v3")
    s = models.BY_ID["large-v3"]
    snap = models.repo_dir(models.hf_cache_dir(s), s.repos[0]) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "weights.npz").write_bytes(b"x")
    (snap / "config.json").write_text("{}")
    assert model_dir("large-v3") == snap
    assert not ins.fix_alignment_heads("large-v3", snap)  # MLX configs are left alone


def test_engine_for_mac(monkeypatch):
    from jamak.core.stt.base import create_recognizer, engine_for

    monkeypatch.setattr(models, "BY_ID", {s.id: s for s in models.specs_for("mac")})
    assert engine_for(SttOptions(model="kotoba-whisper-v2.0")) == "mlx-whisper"
    assert type(create_recognizer(SttOptions(model="large-v3"))).__name__ == "MlxWhisperRecognizer"
    monkeypatch.setattr(hw, "IS_MAC", True)
    assert engine_for(SttOptions(model="/some/folder")) == "mlx-whisper"


# --- downloads --------------------------------------------------------------------------------------

def _fake_download(files: dict[str, bytes]):
    def download(url, dest, sha, total, progress, cancel, done_before=0, grand_total=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(files[url.rsplit("/", 1)[1]])
        return dest
    return download


def test_mac_llama_install_extracts_tarball(jamak_home, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("llama-server", b"#!bin"), ("libllama.1.0.0.dylib", b"lib")):
            info = tarfile.TarInfo(f"llama-b12675/{name}")
            info.size, info.mode = len(data), 0o755
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("llama-b12675/libllama.dylib")
        link.type, link.linkname = tarfile.SYMTYPE, "libllama.1.0.0.dylib"
        tf.addfile(link)
    name = f"llama-{ins.LLAMA_BUILD}-bin-macos-arm64.tar.gz"
    monkeypatch.setattr(hw, "IS_MAC", True)
    monkeypatch.setattr(ins, "_github_release", lambda repo, tag: {"assets": [
        {"name": name, "browser_download_url": f"https://x/{name}", "size": len(buf.getvalue()),
         "digest": "sha256:" + "0" * 64}]})
    monkeypatch.setattr(ins, "download", _fake_download({name: buf.getvalue()}))
    monkeypatch.setattr(type(paths), "llama_server_exe", property(lambda self: self.llama_dir / "llama-server"))
    paths.ensure()
    ins.LlamaComponent(models.BY_ID["llama"]).install(lambda *a: None, threading.Event())
    assert (paths.llama_dir / "llama-server").read_bytes() == b"#!bin"
    assert (paths.llama_dir / "libllama.dylib").read_bytes() == b"lib"  # link (or copy on Windows)
    assert not (paths.llama_dir / "llama-b12675").exists()
    assert '"metal": true' in (paths.llama_dir / "version.json").read_text()


def test_mac_ffmpeg_install(jamak_home, monkeypatch):
    files = {}
    for n in ins.MAC_FFMPEG_FILES:
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr(n, f"{n}-binary")
        files[f"{n}.zip"] = b.getvalue()
    monkeypatch.setattr(hw, "IS_MAC", True)
    monkeypatch.setattr(ins, "download", _fake_download(files))
    paths.ensure()
    ins.FfmpegComponent(models.BY_ID["ffmpeg"]).install(lambda *a: None, threading.Event())
    for n in ("ffmpeg", "ffprobe"):
        assert (paths.bin / "ffmpeg" / n).read_text() == f"{n}-binary"
    assert not list((paths.bin / "downloads").glob("*.zip"))


def test_mac_download_patterns():
    assert set(ins.HF_PATTERNS["mlx-whisper"]) == {"config.json", "weights.npz", "weights.safetensors"}
