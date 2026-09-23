# AETHEL v2: start the desktop app (Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1 [-BackendOnly]
#
# Default: runs the Tauri app, which launches the backend itself and passes it a
# fresh auth token. -BackendOnly: runs just the API on http://127.0.0.1:8765
# with auth disabled (AETHEL_DEV=1), for browser development with `pnpm dev`.

param([switch]$BackendOnly)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ($BackendOnly) {
    $python = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } else { "py" }
    $pyArgs = if ($python -eq "py") { @("-3.11", "-m", "aethel") } else { @("-m", "aethel") }
    $env:AETHEL_DEV = "1"
    Write-Host "Aethel backend on http://127.0.0.1:8765 (dev mode, auth disabled)" -ForegroundColor Cyan
    Push-Location "$Root\backend"
    try { & $python @pyArgs } finally { Pop-Location }
    exit $LASTEXITCODE
}

Push-Location "$Root\frontend_app"
try { pnpm tauri dev } finally { Pop-Location }
