# PyInstaller spec for the packaged backend (onedir). Build with:  uv run pyinstaller packaging/jamak-backend.spec
# Not bundled on purpose (downloaded on first run, see src/jamak/installer.py): NVIDIA DLLs, FFmpeg,
# llama-server, models. Vocal separation (torch/demucs, ~4 GB) is left out of the installer.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

hidden = (
    collect_submodules("jamak")
    + collect_submodules("uvicorn")
    + collect_submodules("anthropic")
    + collect_submodules("google.genai")
    + ["websockets", "websockets.legacy", "httptools", "h11"]
)

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime"),
    datas=collect_data_files("faster_whisper") + collect_data_files("pysubs2"),
    hiddenimports=hidden,
    excludes=[
        "torch", "torchaudio", "demucs", "nvidia", "tkinter", "matplotlib", "IPython",
        "pytest", "pyopenjtalk", "ruff", "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jamak-backend", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="jamak-backend", upx=False)
