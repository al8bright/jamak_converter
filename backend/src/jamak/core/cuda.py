"""Make the NVIDIA DLLs (cuBLAS, cuDNN) loadable on Windows.

Two places are searched: the `nvidia-*-cu12` wheels in site-packages (development) and
`<home>/bin/cuda`, where the first-run installer puts the same DLLs (packaged app).
CTranslate2 (faster-whisper) loads cuDNN sub-libraries lazily via LoadLibrary, so both
os.add_dll_directory and PATH are needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_added: set[str] = set()


def nvidia_dll_dirs() -> list[Path]:
    from jamak.config import paths

    dirs: list[Path] = []
    local = paths.bin / "cuda"
    if local.is_dir() and any(local.glob("*.dll")):
        dirs.append(local)
    for entry in sys.path:
        root = Path(entry) / "nvidia"
        if not root.is_dir():
            continue
        for lib in root.iterdir():
            bin_dir = lib / "bin"
            if bin_dir.is_dir() and any(bin_dir.glob("*.dll")):
                dirs.append(bin_dir)
    return dirs


def find_dll_dir(names: tuple[str, ...]) -> Path | None:
    """First searched directory set that provides all `names` (they may be split across dirs)."""
    dirs = nvidia_dll_dirs()
    found = {n for d in dirs for n in names if (d / n).exists()}
    return dirs[0] if dirs and found == set(names) else None


def setup(force: bool = False) -> None:
    """Idempotent; `force` re-scans after the installer added DLLs."""
    if sys.platform != "win32":
        return
    if _added and not force:
        return
    new = [d for d in nvidia_dll_dirs() if str(d) not in _added]
    for d in new:
        os.add_dll_directory(str(d))
        _added.add(str(d))
    if new:
        os.environ["PATH"] = os.pathsep.join([str(d) for d in new] + [os.environ.get("PATH", "")])
    _added.add("")  # mark as done even when nothing was found


def cuda_device_count() -> int:
    setup()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0
