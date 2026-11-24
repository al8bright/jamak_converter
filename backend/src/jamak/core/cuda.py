"""Make the pip-installed NVIDIA DLLs (cuBLAS, cuDNN) loadable on Windows.

The `nvidia-*-cu12` wheels put their DLLs in site-packages/nvidia/<lib>/bin, which is not
on the DLL search path. CTranslate2 (faster-whisper) loads cuDNN sub-libraries lazily via
LoadLibrary, so both os.add_dll_directory and PATH are needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def nvidia_dll_dirs() -> list[Path]:
    dirs: list[Path] = []
    for entry in sys.path:
        root = Path(entry) / "nvidia"
        if not root.is_dir():
            continue
        for lib in root.iterdir():
            bin_dir = lib / "bin"
            if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
                dirs.append(bin_dir)
    return dirs


def setup() -> None:
    global _done
    if _done or sys.platform != "win32":
        _done = True
        return
    dirs = nvidia_dll_dirs()
    for d in dirs:
        os.add_dll_directory(str(d))
    if dirs:
        os.environ["PATH"] = os.pathsep.join([str(d) for d in dirs] + [os.environ.get("PATH", "")])
    _done = True


def cuda_device_count() -> int:
    setup()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0
