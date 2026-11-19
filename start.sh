#!/usr/bin/env bash
# One-command startup for Linux / macOS: ./start.sh
set -e
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[ERROR] Python 3.10+ not found. Install it from https://www.python.org/downloads/" >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  "$PY" -m venv .venv
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example - add your free API key there, or use the 'API Keys' button in the app."
fi

echo "Installing / checking dependencies (first run takes a few minutes)..."
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

echo "Starting server on http://127.0.0.1:8001  (Ctrl+C to stop)"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
