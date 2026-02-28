#!/usr/bin/env bash
# Start the Venice Codex Proxy in the foreground.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required but was not found in PATH."
  exit 1
fi

if [[ ! -d "$DIR/venv" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv "$DIR/venv"
fi

if [[ ! -f "$DIR/requirements.txt" ]]; then
  echo "ERROR: requirements.txt is missing."
  exit 1
fi

if ! "$DIR/venv/bin/python" -c "import aiohttp" >/dev/null 2>&1; then
  echo "Installing dependencies..."
  "$DIR/venv/bin/python" -m pip install --quiet --upgrade pip
  "$DIR/venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi

if [[ ! -f "$DIR/.env" ]] && [[ -z "${VENICE_API_KEY:-}" ]]; then
  cp "$DIR/.env.example" "$DIR/.env"
  echo "Created .env from .env.example. Add your Venice API key and rerun ./start.sh"
  exit 1
fi

exec "$DIR/venv/bin/python" "$DIR/proxy.py" "$@"
