#!/usr/bin/env bash
# Build the macOS app (Apple Silicon): PyInstaller backend, then the Tauri .app and .dmg.
#
# Output: app/src-tauri/target/release/bundle/{macos/jamak.app,dmg/jamak_<version>_aarch64.dmg}
#
# Like the Windows installer, the bundle holds the app and the Python backend only. FFmpeg,
# llama-server (Metal build) and the models are downloaded on first run, each verified with SHA-256.
#
# Not signed yet: Gatekeeper blocks an unsigned app downloaded from the internet. For a test on your
# own Mac, open it once with right-click > Open. Signing and notarization: TODO.md 4장.
#
# Needs: macOS 14+ on Apple Silicon (mlx has no wheels for older versions), uv, pnpm, Rust (stable, aarch64-apple-darwin).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "build_app.sh builds the Apple Silicon app; run scripts/build_app.ps1 on Windows" >&2
  exit 1
fi

cd "$root/backend"
uv sync --inexact
uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/jamak-backend.spec
./dist/jamak-backend/jamak-backend --help > /dev/null
for f in qwen_asr_worker.py qwen-requirements.lock; do
  [[ -f "dist/jamak-backend/_internal/jamak/runtime/$f" ]] || { echo "runtime/$f is missing from the bundle" >&2; exit 1; }
done
# mlx needs its Metal kernels next to libmlx; without them speech recognition fails on the first job
find dist/jamak-backend/_internal -name "*.metallib" | grep -q . || { echo "mlx.metallib is missing from the bundle" >&2; exit 1; }
./dist/jamak-backend/jamak-backend doctor --bundled || { echo "mlx-whisper does not load in the bundle" >&2; exit 1; }

# Licenses of everything the app ships (THIRD_PARTY_NOTICES.md explains them)
lic="dist/jamak-backend/licenses"
mkdir -p "$lic"
cp "$root/LICENSE" "$lic/LICENSE.txt"
cp "$root/THIRD_PARTY_NOTICES.md" "$lic/"
uv export --no-dev --no-hashes --format requirements-txt |
  uv run python "$root/scripts/collect_licenses.py" python "$lic/python-packages.txt"
uv run python "$root/scripts/collect_licenses.py" cargo "$root/app/src-tauri" "$lic/rust-crates.txt"

cd "$root/app"
pnpm install --frozen-lockfile
# needs node_modules, so it runs here rather than with the other licenses
uv run --project "$root/backend" python "$root/scripts/collect_licenses.py" npm . "$root/backend/$lic/js-packages.txt"
pnpm tauri build

ls -lh "$root"/app/src-tauri/target/release/bundle/dmg/*.dmg
