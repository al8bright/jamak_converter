"""Downloads: everything large or separately licensed is fetched here (first run, or later from
설정 › 모델 관리) instead of being shipped in the installer. What can be downloaded is listed in models.py.

Every download is verified with SHA-256 from the publisher's own metadata (GitHub release
asset digests, PyPI wheel digests, Hugging Face LFS object ids), written to a `.part` file
first (resumable), and only moved into place after the check passes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from jamak import models
from jamak.config import DEFAULT_LLM_FILE, default_llm_path, paths
from jamak.models import Spec

LLAMA_BUILD = "b12675"
LLAMA_CUDA = "12.4"
CUDA_WHEELS = {  # the versions faster-whisper/CTranslate2 is tested with in this repo (see uv.lock)
    "nvidia-cublas-cu12": "12.9.2.10",
    "nvidia-cudnn-cu12": "9.28.1.4",
}
CUDA_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
LLM_REPO = "google/gemma-4-12B-it-qat-q4_0-gguf"
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


def has_nvidia_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


def _dir_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


class Component:
    def __init__(self, spec: Spec):
        self.spec = spec

    @property
    def id(self) -> str:
        return self.spec.id

    def present(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def install(self, progress: Progress, cancel: threading.Event) -> None:  # pragma: no cover
        raise NotImplementedError

    def remove(self) -> None:  # pragma: no cover
        raise NotImplementedError

    def disk_bytes(self) -> int:
        return 0


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

    def disk_bytes(self) -> int:
        return _dir_bytes(paths.bin / "ffmpeg")


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

    def remove(self) -> None:
        shutil.rmtree(paths.llama_dir, ignore_errors=True)

    def disk_bytes(self) -> int:
        return _dir_bytes(paths.llama_dir)


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

    def remove(self) -> None:
        shutil.rmtree(paths.bin / "cuda", ignore_errors=True)

    def disk_bytes(self) -> int:
        return _dir_bytes(paths.bin / "cuda")


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

    def remove(self) -> None:
        (paths.models / DEFAULT_LLM_FILE).unlink(missing_ok=True)

    def disk_bytes(self) -> int:
        p = paths.models / DEFAULT_LLM_FILE
        return p.stat().st_size if p.exists() else 0


# faster-whisper conversions whose alignment heads point at decoder layers the model does not have
# (copied from large-v3): word timestamps then crash CTranslate2 (segfault). Use the last decoder layer.
ALIGNMENT_FIX: dict[str, list[list[int]]] = {
    "kotoba-whisper-v2.0": [[1, h] for h in range(20)],  # 2 decoder layers x 20 heads
}


def fix_alignment_heads(model_id: str, model_dir: Path) -> bool:
    heads = ALIGNMENT_FIX.get(model_id)
    cfg = model_dir / "config.json"
    if not heads or not cfg.exists():
        return False
    data = json.loads(cfg.read_text(encoding="utf-8"))
    if data.get("alignment_heads") == heads:
        return False
    data["alignment_heads"] = heads
    text = json.dumps(data, indent=1)
    if cfg.is_symlink():  # never write through into the shared blob
        cfg.unlink()
    cfg.write_text(text, encoding="utf-8")
    return True


HF_PATTERNS = {
    "faster-whisper": ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
    "qwen-asr": ["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken", "merges.txt", "vocab*"],
}


class HfModelComponent(Component):
    """Speech-recognition model: one or more Hugging Face repos in the huggingface_hub cache layout."""

    def present(self) -> bool:
        return models.local_dirs(self.spec) is not None

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        import fnmatch
        import os

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        from huggingface_hub import snapshot_download

        cache = models.hf_cache_dir(self.spec)
        patterns = HF_PATTERNS[self.spec.engine or "faster-whisper"]
        sizes = {}
        with _client() as c:
            for repo in self.spec.repos:
                r = c.get(f"https://huggingface.co/api/models/{repo}/tree/main")
                r.raise_for_status()
                sizes[repo] = sum(int(f.get("size") or 0) for f in r.json()
                                  if any(fnmatch.fnmatch(f["path"], p) for p in patterns))
        total = sum(sizes.values()) or self.spec.approx_bytes
        done_before = 0
        for repo in self.spec.repos:
            err: list[BaseException] = []

            def run(repo: str = repo, err: list[BaseException] = err) -> None:
                try:
                    snapshot_download(repo, cache_dir=str(cache), allow_patterns=patterns)
                except BaseException as e:  # noqa: BLE001 - re-raised in the caller thread
                    err.append(e)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            rdir = models.repo_dir(cache, repo)
            while t.is_alive():
                if cancel.is_set():
                    raise Cancelled()  # the download thread finishes in the background; .incomplete files resume
                progress(min(done_before + _dir_bytes(rdir), total), total, repo.split("/")[-1])
                t.join(0.5)
            if err:
                raise InstallError(f"{repo} download failed: {err[0]}")
            done_before += sizes.get(repo, 0)
        dirs = models.local_dirs(self.spec)
        if dirs is None:
            raise InstallError(f"{self.spec.name}: downloaded files are incomplete")
        fix_alignment_heads(self.spec.id, dirs[0])
        progress(total, total, self.spec.name)

    def remove(self) -> None:
        cache = models.hf_cache_dir(self.spec)
        for repo in self.spec.repos:
            shutil.rmtree(models.repo_dir(cache, repo), ignore_errors=True)

    def disk_bytes(self) -> int:
        cache = models.hf_cache_dir(self.spec)
        return sum(_dir_bytes(models.repo_dir(cache, r)) for r in self.spec.repos)


UV_BUILD = "0.13.9"
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"


def torch_runtime_dir() -> Path:
    return paths.bin / "torch-runtime"


def torch_python() -> Path:
    return torch_runtime_dir() / "Scripts" / "python.exe"


def runtime_file(name: str) -> Path:
    from importlib.resources import files

    return Path(str(files("jamak.runtime") / name))


class TorchComponent(Component):
    """A separate Python 3.12 + PyTorch environment for engines the backend exe cannot carry (Qwen3-ASR).

    uv (pinned release, SHA-256 checked) creates the environment; every package comes from a lock
    file with hashes (runtime/qwen-requirements.lock), so the install is the same on every PC.
    """

    def present(self) -> bool:
        return torch_python().exists() and (torch_runtime_dir() / "jamak-runtime.json").exists()

    def _uv(self, progress: Progress, cancel: threading.Event) -> Path:
        exe = paths.bin / "uv" / "uv.exe"
        if exe.exists():
            return exe
        rel = _github_release("astral-sh/uv", UV_BUILD)
        asset = next((a for a in rel["assets"] if a["name"] == "uv-x86_64-pc-windows-msvc.zip"), None)
        if asset is None:
            raise InstallError(f"uv {UV_BUILD} has no Windows build")
        z = download(asset["browser_download_url"], paths.bin / "downloads" / asset["name"], _asset_sha(asset),
                     asset["size"], lambda d, t, m: progress(0, self.spec.approx_bytes, "uv"), cancel)
        exe.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                if Path(info.filename).name in ("uv.exe", "LICENSE-MIT", "LICENSE-APACHE"):
                    (exe.parent / Path(info.filename).name).write_bytes(zf.read(info))
        z.unlink(missing_ok=True)
        return exe

    def _run(self, args: list[str], env: dict[str, str], cancel: threading.Event, watch: list[Path],
             progress: Progress, msg: str) -> None:
        from jamak.core.proc import CREATE_NO_WINDOW, bind_to_lifetime

        p = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        bind_to_lifetime(p)
        tail: list[str] = []

        def read() -> None:
            assert p.stdout is not None
            for line in p.stdout:
                tail.append(line.rstrip())
                del tail[:-20]

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        total = self.spec.approx_bytes
        while p.poll() is None:
            if cancel.is_set():
                p.kill()
                raise Cancelled()
            progress(min(sum(_dir_bytes(w) for w in watch), total - 1), total, msg)
            time.sleep(1.0)
        reader.join(2)
        if p.returncode != 0:
            raise InstallError(f"{msg} failed: " + " / ".join(t for t in tail[-5:] if t))

    def install(self, progress: Progress, cancel: threading.Event) -> None:
        import os

        uv = self._uv(progress, cancel)
        cache = paths.bin / "downloads" / "uv-cache"
        env = {**os.environ, "UV_CACHE_DIR": str(cache), "UV_PYTHON_INSTALL_DIR": str(paths.bin / "python"),
               "UV_PYTHON_PREFERENCE": "only-managed", "UV_NO_PROGRESS": "1", "UV_NO_CONFIG": "1"}
        staging = paths.bin / "torch-runtime.new"
        shutil.rmtree(staging, ignore_errors=True)
        lock = runtime_file("qwen-requirements.lock")
        self._run([str(uv), "venv", "--quiet", "--python", "3.12", str(staging)], env, cancel, [cache], progress,
                  "Python 3.12")
        self._run([str(uv), "pip", "install", "--python", str(staging / "Scripts" / "python.exe"), "--no-deps",
                   "--require-hashes", "--index-url", "https://pypi.org/simple", "--extra-index-url", TORCH_INDEX,
                   "--index-strategy", "unsafe-best-match", "--link-mode", "copy", "-r", str(lock)],
                  env, cancel, [cache], progress, "PyTorch")
        check = subprocess.run([str(staging / "Scripts" / "python.exe"), "-c",
                                "import torch, qwen_asr; print(torch.__version__, torch.cuda.is_available())"],
                               capture_output=True, text=True, timeout=300)
        if check.returncode != 0:
            raise InstallError(f"PyTorch check failed: {check.stderr.strip()[-300:]}")
        (staging / "jamak-runtime.json").write_text(json.dumps({
            "torch": check.stdout.strip(), "lock_sha256": _sha256(lock), "uv": UV_BUILD}), encoding="utf-8")
        _swap_dir(staging, torch_runtime_dir())
        shutil.rmtree(cache, ignore_errors=True)
        progress(self.spec.approx_bytes, self.spec.approx_bytes, "PyTorch")

    def remove(self) -> None:
        shutil.rmtree(torch_runtime_dir(), ignore_errors=True)
        shutil.rmtree(paths.bin / "python", ignore_errors=True)

    def disk_bytes(self) -> int:
        return _dir_bytes(torch_runtime_dir()) + _dir_bytes(paths.bin / "python")


def _component(s: Spec) -> Component:
    if s.kind == "stt":
        return HfModelComponent(s)
    return {"ffmpeg": FfmpegComponent, "cuda": CudaComponent, "torch": TorchComponent, "llama": LlamaComponent,
            "llm": LlmComponent}[s.id](s)


COMPONENTS: list[Component] = [_component(s) for s in models.SPECS]
_BY_ID = {c.id: c for c in COMPONENTS}


def present(component_id: str) -> bool:
    c = _BY_ID.get(component_id)
    return bool(c and c.present())


def needs(s: Spec, gpu: bool) -> list[str]:
    return [*s.requires, *(s.gpu_requires if gpu else ())]


def with_dependencies(ids: list[str], gpu: bool) -> list[str]:
    """`ids` plus everything they need, in install order (dependencies first)."""
    want: set[str] = set()

    def add(i: str) -> None:
        if i in want or i not in models.BY_ID:
            return
        want.add(i)
        for d in needs(models.BY_ID[i], gpu):
            add(d)

    for i in ids:
        add(i)
    return [s.id for s in models.SPECS if s.id in want]


def recommended_stt(vram_gb: float | None) -> str:
    if vram_gb is None:
        return "large-v3-turbo"
    return "large-v3" if vram_gb >= 8 else "large-v3-turbo"


# ---------------------------------------------------------------------- manager


@dataclass
class ComponentState:
    id: str
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

    def status(self, sizes: bool = False) -> list[dict[str, Any]]:
        gpu = has_nvidia_gpu()
        out = []
        with self.lock:
            busy = self.running()
            presence = {c.id: c.present() for c in self.components}
            for c in self.components:
                st = self.states.get(c.id)
                if st is None or (not busy and st.status not in ("error", "cancelled")):
                    st = ComponentState(c.id, status="present" if presence[c.id] else "missing")
                    self.states[c.id] = st
                s = c.spec
                deps = needs(s, gpu)
                used_by = [o.id for o in self.components if presence[o.id] and c.id in needs(o.spec, gpu)]
                out.append({
                    **st.__dict__, "name": s.name, "note": s.note, "kind": s.kind, "approx_bytes": s.approx_bytes,
                    "license": s.license, "summary": s.summary, "vram_gb": s.vram_gb, "tags": list(s.tags),
                    "requires": deps, "missing_requires": [d for d in deps if not presence.get(d)],
                    "used_by": used_by, "removable": s.removable,
                    "disk_bytes": c.disk_bytes() if sizes and presence[c.id] else None,
                })
        return out

    def ready(self) -> dict[str, Any]:
        """What the first-run screen needs to know: can a job run at all?"""
        gpu = has_nvidia_gpu()
        stt = [s.id for s in models.SPECS if s.kind == "stt"
               and present(s.id) and all(present(d) for d in needs(s, gpu))]
        local = present("llama") and present("llm")
        return {"ffmpeg": present("ffmpeg"), "stt": stt, "local_translation": local, "gpu": gpu,
                "ok": present("ffmpeg") and bool(stt)}

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def install(self, ids: list[str] | None = None) -> list[str]:
        if self.running():
            raise RuntimeError("an installation is already running")
        if ids is None:  # older clients: what a first run needs
            ids = ["ffmpeg", recommended_stt(None)]
        unknown = [i for i in ids if i not in _BY_ID]
        if unknown:
            raise ValueError(f"unknown component: {', '.join(unknown)}")
        order = with_dependencies(ids, has_nvidia_gpu())
        todo = [_BY_ID[i] for i in order if not _BY_ID[i].present()]
        self.cancel_event = threading.Event()
        with self.lock:
            for c in todo:
                self.states[c.id] = ComponentState(c.id, status="queued")
        self.thread = threading.Thread(target=self._run, args=(todo,), name="jamak-setup", daemon=True)
        self.thread.start()
        return [c.id for c in todo]

    def remove(self, component_id: str) -> None:
        c = _BY_ID.get(component_id)
        if c is None:
            raise ValueError("unknown component")
        if not c.spec.removable:
            raise ValueError(f"{c.spec.name}은(는) 지울 수 없습니다")
        if self.running():
            raise RuntimeError("내려받는 중에는 지울 수 없습니다")
        gpu = has_nvidia_gpu()
        users = [o.spec.name for o in self.components if o.present() and component_id in needs(o.spec, gpu)]
        if users:
            raise ValueError(f"{', '.join(users)}이(가) 이 항목을 씁니다. 먼저 지우세요")
        if c.spec.kind == "stt" and c.present():
            others = [o for o in self.components if o.spec.kind == "stt" and o.id != c.id and o.present()]
            if not others:
                raise ValueError("음성 인식 모델이 하나는 있어야 합니다. 다른 모델을 먼저 받으세요")
        c.remove()
        with self.lock:
            self.states.pop(component_id, None)

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self, todo: list[Component]) -> None:
        paths.ensure()
        for c in todo:
            st = self.states[c.id]
            if self.cancel_event.is_set():
                st.status = "cancelled"
                continue
            gpu = has_nvidia_gpu()
            failed = [d for d in needs(c.spec, gpu) if self.states.get(d) and self.states[d].status in ("error", "cancelled")]
            if failed:
                st.status = "error" if not self.cancel_event.is_set() else "cancelled"
                st.error = f"먼저 받아야 하는 {', '.join(models.BY_ID[d].name for d in failed)}을(를) 받지 못했습니다"
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
