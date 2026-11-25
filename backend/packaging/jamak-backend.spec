# PyInstaller spec for the packaged backend (onedir). Build with:  uv run pyinstaller packaging/jamak-backend.spec
# Not bundled on purpose (downloaded on first run, see src/jamak/installer.py): NVIDIA DLLs, FFmpeg,
# llama-server, models. Vocal separation (torch/demucs, ~4 GB) is left out of the installer.
# On macOS (Apple Silicon) the same spec bundles mlx + mlx-whisper (Metal) instead of the CUDA parts;
# mlx-whisper's torch import is only in its conversion script, so torch stays excluded there too.
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

MAC = sys.platform == "darwin"

hidden = (
    collect_submodules("jamak")
    + collect_submodules("uvicorn")
    + collect_submodules("anthropic")
    + collect_submodules("google.genai")
    + ["websockets", "websockets.legacy", "httptools", "h11"]
    + (collect_submodules("mlx_whisper", filter=lambda m: "torch_whisper" not in m)
       + collect_submodules("keyring.backends.macOS") + ["mlx.core", "mlx.nn"] if MAC else [])
)
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
# mlx: libmlx.dylib and mlx.metallib (the Metal kernels, a data file next to it)
datas = collect_data_files("faster_whisper") + collect_data_files("pysubs2")
if MAC:
    binaries += collect_dynamic_libs("mlx")
    datas += collect_data_files("mlx", includes=["**/*.metallib"]) + collect_data_files("mlx_whisper")

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=binaries,
    # runtime/: the Qwen3-ASR worker script and its lock file run in the separate PyTorch environment
    datas=datas + collect_data_files("jamak.runtime", include_py_files=True, includes=["*.py", "*.lock", "*.in"]),
    hiddenimports=hidden,
    excludes=[
        "torch", "torchaudio", "demucs", "nvidia", "tkinter", "matplotlib", "IPython",
        "pytest", "pyopenjtalk", "ruff", "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
# console=True on macOS too: Tauri reads the sidecar's stdout (no window opens for a sidecar)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jamak-backend", console=True, upx=False,
          target_arch="arm64" if MAC else None)
coll = COLLECT(exe, a.binaries, a.datas, name="jamak-backend", upx=False)
