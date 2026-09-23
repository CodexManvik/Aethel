#!/usr/bin/env bash
# AETHEL v2: start the desktop app. --backend-only runs just the API (dev, auth off).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${1:-}" == "--backend-only" ]]; then
  PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="python3"
  cd "$ROOT/backend" && AETHEL_DEV=1 exec "$PY" -m aethel
fi
cd "$ROOT/frontend_app" && exec pnpm tauri dev
