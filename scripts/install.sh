#!/usr/bin/env bash
# AETHEL — one-command setup (Linux / macOS)
# Usage:  bash scripts/install.sh [--cuda]
#
# Installs: Python venv + deps, frontend deps, llama.cpp server binary.
# After install: drop a .gguf model into models/llm/ and run scripts/start.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CUDA=0
[[ "${1:-}" == "--cuda" ]] && CUDA=1

echo ""
echo "=== AETHEL Installer ==="
echo "Project root: $ROOT"
echo ""

# ---------- 1. Python ----------
echo "[1/5] Checking Python..."
PY=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
      PY="$candidate"; break
    fi
  fi
done
if [[ -z "$PY" ]]; then
  echo "Python 3.10+ not found. Install it first." >&2
  exit 1
fi
echo "  Using: $PY ($($PY --version))"

# ---------- 2. Virtual env + deps ----------
echo "[2/5] Creating virtual environment + installing Python deps..."
[[ -d .venv ]] || "$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt

# ---------- 3. Frontend deps ----------
echo "[3/5] Installing frontend dependencies..."
if ! command -v pnpm >/dev/null 2>&1; then
  if command -v corepack >/dev/null 2>&1; then
    corepack enable && corepack prepare pnpm@latest --activate
  else
    echo "pnpm not found. Install Node.js 20+ first (https://nodejs.org)." >&2
    exit 1
  fi
fi
(cd frontend_app && pnpm install)

# ---------- 4. llama.cpp server binary ----------
echo "[4/5] Fetching llama.cpp server binary..."
BIN_DIR="$ROOT/bin/llama"
if [[ -x "$BIN_DIR/llama-server" ]]; then
  echo "  llama-server already present — skipping."
else
  mkdir -p "$BIN_DIR"
  OS="$(uname -s)"
  ARCH="$(uname -m)"
  if [[ "$OS" == "Darwin" ]]; then
    PATTERN="bin-macos-arm64"
    [[ "$ARCH" == "x86_64" ]] && PATTERN="bin-macos-x64"
  else
    PATTERN="bin-ubuntu-x64"
    [[ "$CUDA" == "1" ]] && PATTERN="bin-ubuntu.*cuda.*x64"
  fi
  URL=$(curl -fsSL https://api.github.com/repos/ggml-org/llama.cpp/releases/latest \
    | grep -oE '"browser_download_url": *"[^"]+"' \
    | grep -E "$PATTERN" | grep -E '\.(zip|tar\.gz)"' | head -n1 \
    | sed -E 's/.*"(https[^"]+)"/\1/')
  if [[ -z "$URL" ]]; then
    echo "  Could not find a matching llama.cpp release asset automatically."
    echo "  Download llama-server from https://github.com/ggml-org/llama.cpp/releases"
    echo "  and place it in $BIN_DIR (or build from source: make llama-server)."
  else
    echo "  Downloading $(basename "$URL")..."
    TMP="$(mktemp -d)"
    curl -fsSL "$URL" -o "$TMP/llama.zip"
    if [[ "$URL" == *.tar.gz ]]; then
      tar -xzf "$TMP/llama.zip" -C "$BIN_DIR"
    else
      unzip -oq "$TMP/llama.zip" -d "$BIN_DIR"
    fi
    rm -rf "$TMP"
    # Flatten nested folders if the archive used one.
    if [[ ! -x "$BIN_DIR/llama-server" ]]; then
      NESTED="$(find "$BIN_DIR" -name llama-server -type f | head -n1 || true)"
      [[ -n "$NESTED" ]] && mv "$(dirname "$NESTED")"/* "$BIN_DIR/" || true
    fi
    chmod +x "$BIN_DIR/llama-server" 2>/dev/null || true
    echo "  llama-server installed to $BIN_DIR"
  fi
fi

# ---------- 5. Config ----------
echo "[5/5] Preparing configuration..."
[[ -f .env ]] || { cp .env.example .env; echo "  Created .env from .env.example"; }

echo ""
echo "=== Install complete ==="
echo ""
echo "Next steps:"
echo "  1. Drop a chat model (.gguf) into models/llm/      (required — see models/README.md)"
echo "  2. Optional: Kokoro voice files into models/tts/   (local text-to-speech)"
echo "  3. Optional: SD checkpoint into models/image/      (selfie generation)"
echo "  4. Run:  bash scripts/start.sh"
