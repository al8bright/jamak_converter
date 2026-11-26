<#
.SYNOPSIS
  Build the Windows installer: PyInstaller backend, then the Tauri NSIS bundle.

.DESCRIPTION
  Output: app\src-tauri\target\release\bundle\nsis\jamak_<version>_x64-setup.exe

  The installer holds the app and the Python backend only (about 80 MB). FFmpeg, the NVIDIA
  CUDA libraries, llama-server and the models are downloaded by the app on first run
  (backend\src\jamak\installer.py), each verified with SHA-256.

  Needs: uv, pnpm, Rust (stable MSVC). Run from anywhere.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $root "backend")
try {
    uv sync --inexact   # keep optional extras (e.g. separation) a developer installed
    if ($LASTEXITCODE) { throw "uv sync failed" }
    uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\jamak-backend.spec
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
    & .\dist\jamak-backend\jamak-backend.exe --help | Out-Null
    if ($LASTEXITCODE) { throw "the packaged backend does not start" }
    & .\dist\jamak-backend\jamak-backend.exe doctor --bundled
    if ($LASTEXITCODE) { throw "the speech-recognition engine does not load in the bundle" }
    foreach ($f in "qwen_asr_worker.py", "qwen-requirements.lock") {
        if (-not (Test-Path ".\dist\jamak-backend\_internal\jamak\runtime\$f")) { throw "runtime/$f is missing from the bundle" }
    }

    # Licenses of everything the installer ships (THIRD_PARTY_NOTICES.md explains them).
    # The NVIDIA wheels are excluded from the bundle (see the spec), so they are left out here too.
    $lic = New-Item -ItemType Directory -Force ".\dist\jamak-backend\licenses"
    Copy-Item (Join-Path $root "LICENSE") (Join-Path $lic "LICENSE.txt") -Force
    Copy-Item (Join-Path $root "THIRD_PARTY_NOTICES.md") $lic -Force
    $collect = Join-Path $root "scripts\collect_licenses.py"
    uv export --no-dev --no-hashes --format requirements-txt `
        --no-emit-package nvidia-cublas-cu12 --no-emit-package nvidia-cudnn-cu12 --no-emit-package nvidia-cuda-nvrtc-cu12 |
        uv run python $collect python (Join-Path $lic "python-packages.txt")
    if ($LASTEXITCODE) { throw "collecting Python package licenses failed" }
    uv run python $collect cargo (Join-Path $root "app\src-tauri") (Join-Path $lic "rust-crates.txt")
    if ($LASTEXITCODE) { throw "collecting Rust crate licenses failed" }
} finally { Pop-Location }

Push-Location (Join-Path $root "app")
try {
    pnpm install --frozen-lockfile
    if ($LASTEXITCODE) { throw "pnpm install failed" }
    # needs node_modules, so it runs here rather than with the other licenses
    $jsLicenses = Join-Path $root "backend\dist\jamak-backend\licenses\js-packages.txt"
    uv run --project (Join-Path $root "backend") python (Join-Path $root "scripts\collect_licenses.py") npm . $jsLicenses
    if ($LASTEXITCODE) { throw "collecting JavaScript package licenses failed" }
    pnpm tauri build
    if ($LASTEXITCODE) { throw "tauri build failed" }
} finally { Pop-Location }

Get-ChildItem (Join-Path $root "app\src-tauri\target\release\bundle\nsis\*.exe") | ForEach-Object {
    "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB)
}
