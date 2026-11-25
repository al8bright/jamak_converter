"""What this computer runs on: the platform, the GPU, and how much memory models may use.

Windows (and Linux for development) use an NVIDIA GPU through CUDA. Macs with Apple Silicon use the
built-in GPU through Metal: speech recognition with mlx-whisper, translation with llama.cpp's Metal
build. Apple GPUs share the main memory ("unified memory"); by default Metal lets the GPU take about
two thirds to three quarters of it, so model sizes are compared against that share.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from typing import Any, Literal

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
METAL_SHARE = 0.7  # part of unified memory the GPU may use (Metal's recommended working set, roughly)

Platform = Literal["win", "mac"]


def current() -> Platform:
    """Which model list and downloads apply: "mac" on macOS, "win" everywhere else (Linux = dev only)."""
    return "mac" if IS_MAC else "win"


def apple_silicon() -> bool:
    return IS_MAC and platform.machine() == "arm64"


def has_nvidia_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


def accelerator() -> Literal["cuda", "metal", "cpu"]:
    if apple_silicon():
        return "metal"
    return "cuda" if has_nvidia_gpu() else "cpu"


def _run(args: list[str], timeout: float = 5) -> str:
    from jamak.core.proc import CREATE_NO_WINDOW

    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


@lru_cache(maxsize=1)
def apple_chip() -> str | None:
    """e.g. "Apple M2 Pro" (None when not on a Mac)."""
    if not IS_MAC:
        return None
    return _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"


def mac_memory_bytes() -> int | None:
    if not IS_MAC:
        return None
    out = _run(["sysctl", "-n", "hw.memsize"])
    return int(out) if out.isdigit() else None


def nvidia_smi(query: str) -> list[list[str]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    out = _run([exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"], timeout=4)
    return [[p.strip() for p in line.split(",")] for line in out.splitlines() if line.strip()]


def gpus() -> list[dict[str, Any]]:
    """GPUs models can run on: NVIDIA cards, or the Apple GPU with its share of unified memory."""
    if apple_silicon():
        total = mac_memory_bytes()
        if not total:
            return []
        return [{"name": apple_chip(), "memory_total_mb": int(total * METAL_SHARE / 2**20), "memory_used_mb": 0,
                 "driver": "Metal", "unified": True, "ram_total_mb": int(total / 2**20)}]
    out = []
    for parts in nvidia_smi("name,memory.total,memory.used,driver_version"):
        if len(parts) == 4:
            try:
                out.append({"name": parts[0], "memory_total_mb": int(float(parts[1])),
                            "memory_used_mb": int(float(parts[2])), "driver": parts[3], "unified": False})
            except ValueError:
                continue
    return out


def model_memory_gb() -> float | None:
    """Memory the GPU can give models: VRAM of the first NVIDIA card, or the Metal share on a Mac."""
    g = gpus()
    return round(g[0]["memory_total_mb"] / 1024, 1) if g else None
