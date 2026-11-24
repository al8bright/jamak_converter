"""First-run downloads: everything large or separately licensed is fetched here instead of
being shipped in the installer.

Every download is verified with SHA-256 from the publisher's own metadata (GitHub release
asset digests, PyPI wheel digests, Hugging Face LFS object ids), written to a `.part` file
first (resumable), and only moved into place after the check passes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from jamak.config import DEFAULT_LLM_FILE, default_llm_path, paths

LLAMA_BUILD = "b12675"
LLAMA_CUDA = "12.4"
CUDA_WHEELS = {  # the versions faster-whisper/CTranslate2 is tested with in this repo (see uv.lock)
    "nvidia-cublas-cu12": "12.9.2.10",
    "nvidia-cudnn-cu12": "9.28.1.4",
}
CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
LLM_REPO = "google/gemma-4-12B-it-qat-q4_0-gguf"
WHISPER_REPO = "Systran/faster-whisper-large-v3"
USER_AGENT = "jamak-installer"

Progress = Callable[[int, int, str], None]  # done bytes, total bytes, message


class Cancelled(Exception):
    pass


class InstallError(Exception):
    pass


# ---------------------------------------------------------------------- download helpers


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0),
                        headers={"user-agent": USER_AGENT})


def _github_release(repo: str, tag: str | None) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/" + (f"tags/{tag}" if tag else "latest")
    with _client() as c:
        r = c.get(url, headers={"accept": "application/vnd.github+json"})
        r.raise_for_status()
        return r.json()


def _asset_sha(asset: dict[str, Any]) -> str:
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise InstallError(f"no SHA-256 published for {asset.get('name')}")
    return digest[7:].lower()


def download(url: str, dest: Path, sha256: str, total: int, progress: Progress, cancel: threading.Event,
             done_before: int = 0, grand_total: int | None = None) -> Path:
    """Resumable download to `dest`, verified against `sha256`. Reports progress offset by
    `done_before` so several files can share one progress bar."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == total and _sha256(dest) == sha256:
        return dest
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    have = 0
    if part.exists():
        with part.open("rb") as f:
            for chunk in iter(lambda: f.read(8 << 20), b""):
                h.update(chunk)
                have += len(chunk)
        if have > total:
            part.unlink()
            h, have = hashlib.sha256(), 0
    gt = grand_total or total
    with _client() as c:
        headers = {"range": f"bytes={have}-"} if have else {}
        with c.stream("GET", url, headers=headers) as r:
            if r.status_code == 200 and have:  # server ignored the range: start over
                h, have = hashlib.sha256(), 0
                mode = "wb"
            elif r.status_code in (200, 206):
                mode = "ab" if have else "wb"
            elif r.status_code == 416 and have == total:
                mode = None
            else:
                r.raise_for_status()
                mode = "wb"
            if mode:
                last = 0.0
                with part.open(mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        if cancel.is_set():
                            raise Cancelled()
                        f.write(chunk)
                        h.update(chunk)
                        have += len(chunk)
                        now = time.monotonic()
                        if now - last > 0.25:
                            progress(done_before + have, gt, f"{dest.name}")
                            last = now
    if have != total:
        raise InstallError(f"{dest.name}: got {have} bytes, expected {total}")
    if h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise InstallError(f"{dest.name}: SHA-256 mismatch (file removed, try again)")
    part.replace(dest)
    progress(done_before + total, gt, f"{dest.name}")
    return dest


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _swap_dir(staging: Path, final: Path) -> None:
    old = final.with_name(final.name + ".old")
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        final.rename(old)
    staging.rename(final)
    shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------------- components


@dataclass
class Component:
    id: str
    name: str
    approx_bytes: int
    note: str = ""

    def present(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def install(self, progress: Progress, cancel: threading.Event) -> None:  # pragma: no cover
        raise NotImplementedError


class FfmpegComponent(Component):
    def present(self) -> bool:
        from jamak.core.audio import FfmpegError, ffmpeg_exe, ffprobe_exe

        try:
            ffmpeg_exe()
            ffprobe_exe()
            return True
        except FfmpegError:
            return False

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        rel = _github_release("BtbN/FFmpeg-Builds", None)
        pat = re.compile(r"^ffmpeg-n(\d+)\.(\d+).*-win64-lgpl-shared-\d+\.\d+\.zip$")
        cands = [(tuple(map(int, m.groups())), a) for a in rel["assets"] if (m := pat.match(a["name"]))]
        if not cands:
            raise InstallError("no LGPL Windows FFmpeg build in the latest BtbN release")
        asset = max(cands, key=lambda x: x[0])[1]
        tmp = paths.bin / "downloads"
        z = download(asset["browser_download_url"], tmp / asset["name"], _asset_sha(asset), asset["size"],
                     progress, cancel)
        staging = paths.bin / "ffmpeg.new"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if len(parts) >= 3 and parts[-2] == "bin" and not info.is_dir():
                    (staging / parts[-1]).write_bytes(zf.read(info))
            lic = [i for i in zf.namelist() if i.endswith("LICENSE.txt")]
            if lic:
                (staging / "LICENSE.txt").write_bytes(zf.read(lic[0]))
        (staging / "version.json").write_text(json.dumps({"asset": asset["name"], "sha256": _asset_sha(asset)}),
                                              encoding="utf-8")
        _swap_dir(staging, paths.bin / "ffmpeg")
        z.unlink(missing_ok=True)


class LlamaComponent(Component):
    def present(self) -> bool:
        return paths.llama_server_exe.exists()

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        rel = _github_release("ggml-org/llama.cpp", LLAMA_BUILD)
        want = [f"llama-{LLAMA_BUILD}-bin-win-cuda-{LLAMA_CUDA}-x64.zip", f"cudart-llama-bin-win-cuda-{LLAMA_CUDA}-x64.zip"]
        assets = {a["name"]: a for a in rel["assets"]}
        missing = [w for w in want if w not in assets]
        if missing:
            raise InstallError(f"release {LLAMA_BUILD} has no {missing}")
        total = sum(assets[w]["size"] for w in want)
        tmp = paths.bin / "downloads"
        staging = paths.bin / "llama-server.new"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        done = 0
        files = []
        for w in want:
            a = assets[w]
            z = download(a["browser_download_url"], tmp / w, _asset_sha(a), a["size"], progress, cancel,
                         done_before=done, grand_total=total)
            done += a["size"]
            files.append(z)
            with zipfile.ZipFile(z) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        (staging / Path(info.filename).name).write_bytes(zf.read(info))
        (staging / "version.json").write_text(json.dumps({
            "build": LLAMA_BUILD, "cuda": LLAMA_CUDA,
            "assets": [{"name": w, "sha256": _asset_sha(assets[w])} for w in want],
        }), encoding="utf-8")
        _swap_dir(staging, paths.llama_dir)
        for z in files:
            z.unlink(missing_ok=True)


class CudaComponent(Component):
    def present(self) -> bool:
        from jamak.core import cuda

        return cuda.find_dll_dir(CUDA_DLLS) is not None

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        wheels = []
        with _client() as c:
            for pkg, ver in CUDA_WHEELS.items():
                r = c.get(f"https://pypi.org/pypi/{pkg}/{ver}/json")
                r.raise_for_status()
                win = [u for u in r.json()["urls"] if u["filename"].endswith("win_amd64.whl")]
                if not win:
                    raise InstallError(f"{pkg} {ver} has no Windows wheel")
                wheels.append(win[0])
        total = sum(w["size"] for w in wheels)
        tmp = paths.bin / "downloads"
        staging = paths.bin / "cuda.new"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        done = 0
        for w in wheels:
            f = download(w["url"], tmp / w["filename"], w["digests"]["sha256"], w["size"], progress, cancel,
                         done_before=done, grand_total=total)
            done += w["size"]
            with zipfile.ZipFile(f) as zf:
                for info in zf.infolist():
                    p = Path(info.filename)
                    if p.suffix == ".dll" and "bin" in p.parts and not p.name.startswith("nvblas"):
                        (staging / p.name).write_bytes(zf.read(info))
                    elif p.name == "LICENSE.txt" or p.name.startswith("License"):
                        (staging / f"{p.parts[0]}-{p.name}").write_bytes(zf.read(info))
            f.unlink(missing_ok=True)
        _swap_dir(staging, paths.bin / "cuda")
        from jamak.core import cuda

        cuda.setup(force=True)


class LlmComponent(Component):
    def present(self) -> bool:
        return default_llm_path() is not None

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        with _client() as c:
            r = c.get(f"https://huggingface.co/api/models/{LLM_REPO}/tree/main")
            r.raise_for_status()
            files = {f["path"]: f for f in r.json()}
        f = files.get(DEFAULT_LLM_FILE)
        if not f or not f.get("lfs"):
            raise InstallError(f"{DEFAULT_LLM_FILE} not found in {LLM_REPO}")
        download(f"https://huggingface.co/{LLM_REPO}/resolve/main/{DEFAULT_LLM_FILE}", paths.models / DEFAULT_LLM_FILE,
                 f["lfs"]["oid"], f["size"], progress, cancel)


class WhisperComponent(Component):
    def _dir(self) -> Path:
        return paths.whisper_models / ("models--" + WHISPER_REPO.replace("/", "--"))

    def present(self) -> bool:
        return any(self._dir().glob("snapshots/*/model.bin"))

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        # Same cache layout faster-whisper uses (huggingface_hub cache_dir), so STT finds it.
        import os

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        from huggingface_hub import snapshot_download

        with _client() as c:
            r = c.get(f"https://huggingface.co/api/models/{WHISPER_REPO}/tree/main")
            r.raise_for_status()
            total = sum(int(f.get("size") or 0) for f in r.json())
        err: list[BaseException] = []

        def run() -> None:
            try:
                snapshot_download(WHISPER_REPO, cache_dir=str(paths.whisper_models),
                                  allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                                                  "tokenizer.json", "vocabulary.*"])
            except BaseException as e:  # noqa: BLE001 - re-raised in the caller thread
                err.append(e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        while t.is_alive():
            if cancel.is_set():
                raise Cancelled()  # the download thread finishes in the background; .incomplete files resume
            size = sum(p.stat().st_size for p in self._dir().rglob("*") if p.is_file()) if self._dir().exists() else 0
            progress(min(size, total), total, WHISPER_REPO.split("/")[-1])
            t.join(0.5)
        if err:
            raise InstallError(f"Whisper download failed: {err[0]}")
        progress(total, total, WHISPER_REPO.split("/")[-1])


GB = 1_000_000_000
COMPONENTS: list[Component] = [
    FfmpegComponent("ffmpeg", "FFmpeg (영상에서 소리 꺼내기)", 80_000_000, "LGPL 빌드, BtbN/FFmpeg-Builds"),
    CudaComponent("cuda", "NVIDIA CUDA 라이브러리 (음성 인식 가속)", 1_250_000_000, "cuBLAS, cuDNN (PyPI)"),
    LlamaComponent("llama", "llama-server (번역 엔진)", 700_000_000, f"llama.cpp {LLAMA_BUILD}, CUDA {LLAMA_CUDA}"),
    WhisperComponent("whisper", "음성 인식 모델 Whisper large-v3", 3_090_000_000, "MIT"),
    LlmComponent("llm", "번역 모델 Gemma 4 12B (QAT Q4_0)", 6_980_000_000, "Gemma 라이선스, Google"),
]


# ---------------------------------------------------------------------- manager


@dataclass
class ComponentState:
    id: str
    name: str
    note: str
    approx_bytes: int
    status: str = "missing"  # present | missing | queued | running | done | error | cancelled
    done_bytes: int = 0
    total_bytes: int = 0
    message: str = ""
    error: str | None = None


@dataclass
class SetupManager:
    components: list[Component] = field(default_factory=lambda: COMPONENTS)
    states: dict[str, ComponentState] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None

    def status(self) -> list[dict[str, Any]]:
        out = []
        with self.lock:
            busy = self.running()
            for c in self.components:
                st = self.states.get(c.id)
                if st is None or (not busy and st.status not in ("error", "cancelled")):
                    st = ComponentState(c.id, c.name, c.note, c.approx_bytes,
                                        status="present" if c.present() else "missing")
                    self.states[c.id] = st
                out.append(dict(st.__dict__))
        return out

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def install(self, ids: list[str] | None = None) -> None:
        if self.running():
            raise RuntimeError("an installation is already running")
        todo = [c for c in self.components if (ids is None or c.id in ids) and not c.present()]
        self.cancel_event = threading.Event()
        with self.lock:
            for c in todo:
                self.states[c.id] = ComponentState(c.id, c.name, c.note, c.approx_bytes, status="queued")
        self.thread = threading.Thread(target=self._run, args=(todo,), name="jamak-setup", daemon=True)
        self.thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self, todo: list[Component]) -> None:
        paths.ensure()
        for c in todo:
            st = self.states[c.id]
            if self.cancel_event.is_set():
                st.status = "cancelled"
                continue
            st.status = "running"

            def progress(done: int, total: int, msg: str, st: ComponentState = st) -> None:
                st.done_bytes, st.total_bytes, st.message = done, total, msg

            try:
                c.install(progress, self.cancel_event)
                st.status = "done"
            except Cancelled:
                st.status = "cancelled"
            except Exception as e:  # noqa: BLE001 - reported to the UI
                st.status = "error"
                st.error = f"{type(e).__name__}: {e}"
