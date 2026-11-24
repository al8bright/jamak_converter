<#
.SYNOPSIS
  Download a pinned llama.cpp Windows CUDA build (llama-server.exe + CUDA runtime DLLs)
  into the jamak_converter runtime folder. No installer, no admin rights.

.DESCRIPTION
  - Fetches release metadata for -Build from the GitHub API.
  - Downloads the llama.cpp binaries zip and the matching cudart zip.
  - Verifies both against the SHA-256 digests GitHub publishes for the release assets.
  - Extracts into a staging folder, checks that llama-server.exe is present,
    then swaps it into place and writes version.json.
  - Skips everything if the requested build/CUDA pair is already installed (use -Force to redo).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup_llama.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup_llama.ps1 -Build b12700 -Force
#>
[CmdletBinding()]
param(
    # Pinned, verified build. Bump deliberately, then re-run the Phase 0 checks (PLAN.md).
    [string]$Build = "b12675",
    # "12.4" works with older drivers too and matches faster-whisper's CUDA 12 stack.
    [ValidateSet("12.4", "13.5")]
    [string]$Cuda = "12.4",
    [string]$Dest = (Join-Path $env:LOCALAPPDATA "jamak_converter\bin\llama-server"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar on PS 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "ggml-org/llama.cpp"
$BinAsset = "llama-$Build-bin-win-cuda-$Cuda-x64.zip"
$RtAsset = "cudart-llama-bin-win-cuda-$Cuda-x64.zip"
$VersionFile = Join-Path $Dest "version.json"

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# --- already installed? -------------------------------------------------------
if ((Test-Path $VersionFile) -and -not $Force) {
    $cur = Get-Content $VersionFile -Raw | ConvertFrom-Json
    if ($cur.build -eq $Build -and $cur.cuda -eq $Cuda -and (Test-Path (Join-Path $Dest "llama-server.exe"))) {
        Write-Host "llama-server $Build (CUDA $Cuda) is already installed at $Dest. Use -Force to reinstall."
        exit 0
    }
}

# --- release metadata ---------------------------------------------------------
Write-Step "Fetching release metadata for $Build"
$headers = @{ "User-Agent" = "jamak-converter-setup"; "Accept" = "application/vnd.github+json" }
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/$Build" -Headers $headers

function Get-Asset([string]$name) {
    $a = $release.assets | Where-Object { $_.name -eq $name }
    if (-not $a) { throw "Asset '$name' not found in release $Build." }
    if (-not $a.digest -or -not $a.digest.StartsWith("sha256:")) { throw "No SHA-256 digest published for '$name'." }
    return $a
}
$assets = @((Get-Asset $BinAsset), (Get-Asset $RtAsset))

# --- download + verify --------------------------------------------------------
$work = Join-Path ([IO.Path]::GetTempPath()) ("jamak-llama-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    foreach ($a in $assets) {
        $file = Join-Path $work $a.name
        $mb = [math]::Round($a.size / 1MB)
        Write-Step "Downloading $($a.name) ($mb MB)"
        Invoke-WebRequest -Uri $a.browser_download_url -OutFile $file -UseBasicParsing -Headers @{ "User-Agent" = "jamak-converter-setup" }

        $expected = $a.digest.Substring(7).ToLowerInvariant()
        $actual = (Get-FileHash -Path $file -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "SHA-256 mismatch for $($a.name): expected $expected, got $actual" }
        Write-Host "    sha256 OK"
    }

    # --- extract into staging -------------------------------------------------
    $staging = "$Dest.staging"
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    foreach ($a in $assets) {
        Write-Step "Extracting $($a.name)"
        Expand-Archive -Path (Join-Path $work $a.name) -DestinationPath $staging -Force
    }

    # Some builds nest files in a subfolder: flatten to the folder holding llama-server.exe.
    $exe = Get-ChildItem -Path $staging -Recurse -Filter "llama-server.exe" | Select-Object -First 1
    if (-not $exe) { throw "llama-server.exe not found in the extracted archives." }
    if ($exe.DirectoryName -ne (Resolve-Path $staging).Path) {
        Get-ChildItem -Path $staging -Recurse -Filter "*.dll" |
            Where-Object { $_.DirectoryName -ne $exe.DirectoryName } |
            Move-Item -Destination $exe.DirectoryName -Force
        $root = $exe.DirectoryName
    } else {
        $root = $staging
    }

    [ordered]@{
        build     = $Build
        cuda      = $Cuda
        installed = (Get-Date).ToString("s")
        assets    = @($assets | ForEach-Object { [ordered]@{ name = $_.name; sha256 = $_.digest.Substring(7) } })
    } | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $root "version.json") -Encoding UTF8

    # --- swap into place ------------------------------------------------------
    Write-Step "Installing to $Dest"
    if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path $Dest -Parent) -Force | Out-Null
    Move-Item -Path $root -Destination $Dest
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
}
finally {
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

# --- smoke test ---------------------------------------------------------------
$server = Join-Path $Dest "llama-server.exe"
Write-Step "Smoke test: llama-server --version / --list-devices"
& $server --version
& $server --list-devices
if ($LASTEXITCODE -ne 0) {
    Write-Warning "llama-server --list-devices exited with $LASTEXITCODE. Check the NVIDIA driver supports CUDA $Cuda."
    exit 1
}
Write-Host ""
Write-Host "Done. llama-server $Build (CUDA $Cuda) -> $server" -ForegroundColor Green
