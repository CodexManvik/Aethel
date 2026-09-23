# AETHEL — one-command setup (Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-Cuda]
#
# Installs: Python venv + deps, frontend deps, llama.cpp server binary.
# After install: drop a .gguf model into models\llm\ and run scripts\start.ps1

param(
    [switch]$Cuda   # download the CUDA build of llama.cpp instead of CPU
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "`n=== AETHEL Installer ===" -ForegroundColor Magenta
Write-Host "Project root: $Root`n"

# ---------- 1. Python ----------
Write-Host "[1/5] Checking Python..." -ForegroundColor Cyan
$py = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3.10", "python")) {
    try {
        $ver = Invoke-Expression "$candidate --version 2>&1"
        if ($ver -match "Python 3\.(1[0-9])") { $py = $candidate; break }
    } catch {}
}
if (-not $py) {
    Write-Host "Python 3.10+ not found. Install it from https://python.org (check 'Add to PATH')." -ForegroundColor Red
    exit 1
}
Write-Host "  Using: $py ($(Invoke-Expression "$py --version 2>&1"))"

# ---------- 2. Virtual env + deps ----------
Write-Host "[2/5] Creating virtual environment + installing Python deps..." -ForegroundColor Cyan
if (-not (Test-Path "$Root\.venv")) {
    Invoke-Expression "$py -m venv `"$Root\.venv`""
}
& "$Root\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$Root\.venv\Scripts\python.exe" -m pip install -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed." -ForegroundColor Red; exit 1 }

# ---------- 3. Frontend deps ----------
Write-Host "[3/5] Installing frontend dependencies..." -ForegroundColor Cyan
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    if (Get-Command corepack -ErrorAction SilentlyContinue) {
        corepack enable
        corepack prepare pnpm@latest --activate
    } else {
        Write-Host "pnpm not found. Install Node.js 20+ from https://nodejs.org then re-run." -ForegroundColor Red
        exit 1
    }
}
Push-Location "$Root\frontend_app"
pnpm install
$pnpmExit = $LASTEXITCODE
Pop-Location
if ($pnpmExit -ne 0) { Write-Host "pnpm install failed." -ForegroundColor Red; exit 1 }

# ---------- 4. llama.cpp server binary ----------
Write-Host "[4/5] Fetching llama.cpp server binary..." -ForegroundColor Cyan
$binDir = "$Root\bin\llama"
if (Test-Path "$binDir\llama-server.exe") {
    Write-Host "  llama-server.exe already present — skipping."
} else {
    New-Item -ItemType Directory -Force $binDir | Out-Null
    $release = Invoke-RestMethod "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    $pattern = if ($Cuda) { "bin-win.*cuda.*x64" } else { "bin-win.*(cpu|avx2).*x64" }
    $asset = $release.assets | Where-Object { $_.name -match $pattern -and $_.name -match "\.zip$" } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "  Could not find a matching llama.cpp release asset automatically." -ForegroundColor Yellow
        Write-Host "  Download llama-server manually from https://github.com/ggml-org/llama.cpp/releases"
        Write-Host "  and extract it into $binDir"
    } else {
        Write-Host "  Downloading $($asset.name) ($([math]::Round($asset.size/1MB)) MB)..."
        $zipPath = "$env:TEMP\llama_cpp_release.zip"
        Invoke-WebRequest $asset.browser_download_url -OutFile $zipPath
        Expand-Archive $zipPath -DestinationPath $binDir -Force
        Remove-Item $zipPath -Force
        # Some archives nest binaries in a subfolder — flatten if needed.
        if (-not (Test-Path "$binDir\llama-server.exe")) {
            $nested = Get-ChildItem $binDir -Recurse -Filter "llama-server.exe" | Select-Object -First 1
            if ($nested) { Move-Item "$($nested.DirectoryName)\*" $binDir -Force }
        }
        if ($Cuda -and -not $asset.name.Contains("cudart")) {
            $cudart = $release.assets | Where-Object { $_.name -match "cudart.*win" } | Select-Object -First 1
            if ($cudart) {
                Write-Host "  Downloading CUDA runtime ($($cudart.name))..."
                Invoke-WebRequest $cudart.browser_download_url -OutFile $zipPath
                Expand-Archive $zipPath -DestinationPath $binDir -Force
                Remove-Item $zipPath -Force
            }
        }
        Write-Host "  llama-server installed to $binDir"
    }
}

# ---------- 5. Config ----------
Write-Host "[5/5] Preparing configuration..." -ForegroundColor Cyan
if (-not (Test-Path "$Root\.env")) {
    Copy-Item "$Root\.env.example" "$Root\.env"
    Write-Host "  Created .env from .env.example"
}

Write-Host "`n=== Install complete ===" -ForegroundColor Green
Write-Host @"

Next steps:
  1. Drop a chat model (.gguf) into models\llm\      (required — see models\README.md)
  2. Optional: Kokoro voice files into models\tts\   (local text-to-speech)
  3. Optional: SD checkpoint into models\image\      (selfie generation)
  4. Run:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1
"@
