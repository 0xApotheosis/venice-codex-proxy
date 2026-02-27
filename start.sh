#!/bin/bash
# Start the Venice Codex Proxy
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Create venv if missing
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    venv/bin/pip install --quiet aiohttp
fi

exec venv/bin/python proxy.py "$@"
