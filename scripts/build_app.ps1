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
} finally { Pop-Location }

Push-Location (Join-Path $root "app")
try {
    pnpm install --frozen-lockfile
    if ($LASTEXITCODE) { throw "pnpm install failed" }
    pnpm tauri build
    if ($LASTEXITCODE) { throw "tauri build failed" }
} finally { Pop-Location }

Get-ChildItem (Join-Path $root "app\src-tauri\target\release\bundle\nsis\*.exe") | ForEach-Object {
    "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB)
}
