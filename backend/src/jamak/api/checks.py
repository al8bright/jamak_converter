"""System status checks and the live resource monitor shown in the app footer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

import httpx

from jamak.config import default_llm_path, paths
from jamak.core.proc import CREATE_NO_WINDOW
from jamak.core.store import Profile, Store, store

_usage_lock = threading.Lock()
_usage_cache: tuple[float, dict[str, Any]] | None = None


def cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
                return " ".join(str(name).split())
        except OSError:
            pass
    import platform

    return platform.processor() or "CPU"


def short_gpu(name: str) -> str:
    return name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")


def short_cpu(name: str) -> str:
    for junk in ("AMD ", "Intel(R) Core(TM) ", "Intel(R) ", "(R)", "(TM)", " 8-Core Processor",
                 " 6-Core Processor", " 12-Core Processor", " 16-Core Processor", " Processor", " CPU"):
        name = name.replace(junk, " " if junk.startswith(" ") and junk.endswith(" ") else "")
    return " ".join(name.split())


def _nvidia_smi(query: str) -> list[list[str]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        res = subprocess.run([exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=4, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [[p.strip() for p in line.split(",")] for line in res.stdout.strip().splitlines() if line.strip()]


def usage() -> dict[str, Any]:
    """GPU / VRAM / CPU / RAM now. Cached for one second (several windows may poll)."""
    global _usage_cache
    import psutil

    with _usage_lock:
        now = time.monotonic()
        if _usage_cache and now - _usage_cache[0] < 1.0:
            return _usage_cache[1]
        gpu = None
        rows = _nvidia_smi("name,utilization.gpu,memory.used,memory.total")
        if rows and len(rows[0]) == 4:
            r = rows[0]
            try:
                gpu = {"name": short_gpu(r[0]), "util": float(r[1]), "vram_used_gb": float(r[2]) / 1024,
                       "vram_total_gb": float(r[3]) / 1024}
            except ValueError:
                gpu = None
        vm = psutil.virtual_memory()
        data = {
            "gpu": gpu,
            "cpu": psutil.cpu_percent(interval=None),
            "ram_used_gb": (vm.total - vm.available) / 1024**3,
            "ram_total_gb": vm.total / 1024**3,
            "cpu_name": short_cpu(cpu_name()),
        }
        _usage_cache = (now, data)
        return data


def profile_status(p: Profile, db: Store | None = None) -> dict[str, Any]:
    """Short readiness line for a profile (no network calls)."""
    from pathlib import Path

    db = db or store
    lt = p.last_test or {}
    if p.kind == "local_managed":
        mp = Path(p.model) if p.model else default_llm_path()
        if not paths.llama_server_exe.exists():
            return {"ok": False, "ready": False, "text": "로컬 번역 엔진을 받지 않았습니다 · 설정 › 모델 관리"}
        if mp is None or not mp.exists():
            return {"ok": False, "ready": False, "text": "번역 모델을 받지 않았습니다 · 설정 › 모델 관리"}
        return {"ok": True, "ready": True, "text": "준비됨"}
    if p.cloud and not db.has_secret(p):
        return {"ok": False, "ready": False, "text": "등록된 키 없음"}
    if lt:
        return {"ok": bool(lt.get("ok")), "ready": True,
                "text": ("연결 테스트 통과" if lt.get("ok") else lt.get("message", "연결 테스트 실패")),
                "at": lt.get("at")}
    return {"ok": True, "ready": True, "text": "연결 테스트 전"}


def _dir_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def run_checks(sys_info: dict[str, Any], setup_status: list[dict[str, Any]], db: Store | None = None,
               probe_external: bool = True) -> list[dict[str, Any]]:
    import psutil

    db = db or store
    out: list[dict[str, Any]] = []

    def add(state: str, area: str, title: str, detail: str = "", fix: str | None = None,
            bar: float | None = None, action: str | None = None) -> None:
        out.append({"state": state, "area": area, "title": title, "detail": detail, "fix": fix,
                    "bar": bar, "action": action})

    setup = {c["id"]: c for c in setup_status}

    gpus = sys_info.get("gpus") or []
    if gpus:
        g = gpus[0]
        add("ok", "GPU", g["name"], f"드라이버 {g['driver']} · VRAM {round(g['memory_total_mb'] / 1024)} GB")
    else:
        add("warn", "GPU", "NVIDIA GPU를 찾지 못했습니다", "nvidia-smi 없음",
            "GPU가 없으면 음성 인식과 번역을 CPU로 처리해 매우 느립니다.")

    vm = psutil.virtual_memory()
    add("ok", "CPU · 메모리", cpu_name(),
        f"{psutil.cpu_count(logical=False)}코어 {psutil.cpu_count()}스레드 · RAM {vm.total / 1024**3:.1f} GB")

    cuda_c = setup.get("cuda", {})
    if sys_info.get("cuda_devices"):
        add("ok", "CUDA", "faster-whisper · CUDA float16 사용 가능", "cuBLAS 12 · cuDNN 9")
    elif cuda_c.get("status") in ("queued", "running"):
        add("warn", "CUDA", "CUDA 라이브러리 내려받는 중", _progress_text(cuda_c), bar=_bar(cuda_c))
    else:
        add("err" if gpus else "off", "CUDA", "CUDA를 쓸 수 없습니다", "cuBLAS · cuDNN 없음",
            "설정 › 모델 관리에서 CUDA 라이브러리를 받으세요." if gpus else None,
            action="setup" if gpus else None)

    stt = [c for c in setup_status if c.get("kind") == "stt"]
    ready_stt = [c for c in stt if c.get("status") in ("present", "done")]
    for c in stt:
        if c.get("status") in ("present", "done"):
            missing = [setup[d]["name"] for d in c.get("missing_requires", []) if d in setup]
            if missing:
                add("err", "음성 인식 모델", c["name"], f"{', '.join(missing)} 없음",
                    "설정 › 모델 관리에서 받으세요.", action="setup")
            else:
                add("ok", "음성 인식 모델", c["name"], "받아 둠")
        elif c.get("status") in ("queued", "running", "error"):
            _component(add, c, "음성 인식 모델", c["name"], "")
    if not ready_stt and not any(c.get("status") in ("queued", "running") for c in stt):
        add("err", "음성 인식 모델", "받은 모델이 없습니다", "자막을 만들려면 하나가 필요합니다",
            "설정 › 모델 관리에서 받으세요.", action="setup")
    torch_c = setup.get("torch")
    if torch_c and torch_c.get("status") != "missing":
        _component(add, torch_c, "실행 환경", "PyTorch (Qwen3-ASR용)", "bin/torch-runtime")

    llama = sys_info.get("llama_server") or {}
    lc = setup.get("llama", {})
    default = default_llm_path()
    lm = setup.get("llm", {})
    if not llama.get("exists") and default is None and lc.get("status") == "missing" and lm.get("status") == "missing":
        add("off", "로컬 번역", "받지 않음 (선택)", "클라우드 번역만 씁니다",
            "이 PC에서 번역하려면 설정 › 모델 관리에서 번역 모델을 받으세요.", action="setup")
    else:
        if llama.get("exists"):
            build = llama.get("build")
            add("ok", "번역 서버", f"llama-server {build or ''}".strip() + " · CUDA 12.4", "bin/llama-server")
        else:
            _component(add, lc, "번역 서버", "llama-server", "")
        if default is not None:
            add("ok", "번역 모델 (GGUF)", default.name, _gb(default.stat().st_size))
        elif lm:
            _component(add, lm, "번역 모델 (GGUF)", "gemma-4-12b-it-qat-q4_0.gguf", "",
                       wait_fix="다운로드가 끝나면 다시 확인을 누르세요. 그 전까지 로컬 프로필로는 번역할 수 없습니다.")

    profiles = db.list_profiles()
    for p in profiles:
        if p.kind != "local_external":
            continue
        url = (p.base_url or "").rstrip("/")
        if not probe_external:
            add("ok", "외부 서버 프로필", p.name, url)
            continue
        try:
            r = httpx.get(f"{url}/models", timeout=1.5)
            reachable = r.status_code < 500
        except httpx.HTTPError:
            reachable = False
        if reachable:
            add("ok", "외부 서버 프로필", p.name, f"{url} 응답함")
        else:
            add("err", "외부 서버 프로필", p.name, f"{url} 응답 없음",
                "서버가 실행 중인지, 프로필의 서버 주소가 맞는지 확인하세요.")

    from jamak.core import providers

    keys = db.list_keys()
    unused = []
    for info in providers.PROVIDERS:
        kind, title = info.id, info.name
        provider_keys = [k for k in keys if k["provider"] == kind]
        kind_profiles = [p for p in profiles if p.kind == kind]
        env = any(os.environ.get(n) for n in info.env)
        if not provider_keys and not env:
            unused.append(title)
            continue
        detail = " · ".join(f"{k['label']} ••••{k['last4']}" for k in provider_keys) or "환경 변수의 키"
        if not kind_profiles:
            add("warn", "클라우드 번역", f"{title} · 키 {len(provider_keys) or 1}개", detail + " · 이 키를 쓰는 프로필 없음",
                "설정 › 번역 엔진에서 프로필을 만드세요.", action="engine")
            continue
        tested = [p.last_test for p in kind_profiles if p.last_test]
        if any(t and not t.get("ok") for t in tested) and not any(t and t.get("ok") for t in tested):
            add("err", "클라우드 번역", f"{title} · 키 {len(provider_keys) or 1}개", detail + " · 연결 테스트 실패",
                "설정 › 번역 엔진에서 키와 모델을 확인하고 연결 테스트를 다시 하세요.", action="engine")
        elif any(t and t.get("ok") for t in tested):
            add("ok", "클라우드 번역", f"{title} · 키 {len(provider_keys) or 1}개", detail + " · 연결 테스트 통과")
        else:
            add("ok", "클라우드 번역", f"{title} · 키 {len(provider_keys) or 1}개", detail + " · 연결 테스트 전")
    if unused:
        add("off", "클라우드 번역", "키 없는 서비스", ", ".join(unused),
            "쓰려면 설정 › 번역 엔진에서 키를 추가하세요.", action="engine")

    ff = sys_info.get("ffmpeg")
    if ff:
        where = "앱 폴더에서 찾음" if str(paths.bin) in ff else "PATH에서 찾음"
        add("ok", "도구", "FFmpeg · ffprobe", where)
    else:
        _component(add, setup.get("ffmpeg"), "도구", "FFmpeg · ffprobe", "")

    n_jobs, size = 0, 0
    if paths.jobs.exists():
        for d in paths.jobs.iterdir():
            if (d / "meta.json").exists():
                n_jobs += 1
                size += _dir_size(d)
    add("ok", "저장 공간", f"작업 캐시 {n_jobs}개 · {_gb(size)}", "jobs/ · jamak.db",
        "캐시를 지우면 다음 처리 때 모든 단계를 처음부터 다시 실행합니다." if n_jobs else None)
    return out


def _bar(c: dict[str, Any]) -> float | None:
    total = c.get("total_bytes") or c.get("approx_bytes") or 0
    return min(1.0, c.get("done_bytes", 0) / total) if total else None


def _progress_text(c: dict[str, Any]) -> str:
    return f"내려받는 중 · {_gb(c.get('done_bytes', 0))} 받음"


def _component(add, c: dict[str, Any] | None, area: str, title: str, detail: str, wait_fix: str | None = None) -> None:
    if not c:
        add("err", area, title, "없음", "설정 › 모델 관리에서 받으세요.", action="setup")
        return
    st = c.get("status")
    if st in ("present", "done"):
        add("ok", area, title, detail)
    elif st in ("queued", "running"):
        add("warn", area, title, _progress_text(c), wait_fix or "다운로드가 끝나면 다시 확인을 누르세요.", bar=_bar(c))
    elif st == "error":
        add("err", area, title, c.get("error") or "내려받기 실패", "설정 › 모델 관리에서 다시 시도하세요.",
            action="setup")
    else:
        add("err", area, title, f"없음 · 약 {_gb(c.get('approx_bytes', 0))}", "설정 › 모델 관리에서 받으세요.",
            action="setup")
