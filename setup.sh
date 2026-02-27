#!/bin/bash
# One-time setup: creates venv, installs deps, configures Codex, installs LaunchAgent
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== Venice Codex Proxy Setup ==="
echo

# 1. Check .env
if [ ! -f .env ]; then
    echo "ERROR: Create a .env file with your Venice API key."
    echo "  cp .env.example .env && edit .env"
    exit 1
fi

# 2. Create venv + install aiohttp
echo "[1/3] Setting up Python environment..."
python3 -m venv venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet aiohttp
echo "  Done."

# 3. Configure Codex
echo "[2/3] Configuring Codex desktop..."
mkdir -p ~/.codex
CODEX_CONFIG=~/.codex/config.toml

# Back up existing config
if [ -f "$CODEX_CONFIG" ]; then
    cp "$CODEX_CONFIG" "${CODEX_CONFIG}.bak"
    echo "  Backed up existing config to config.toml.bak"
fi

# Check if venice provider already configured
if grep -q "model_providers.venice" "$CODEX_CONFIG" 2>/dev/null; then
    echo "  Venice provider already in config.toml — skipping."
else
    cat >> "$CODEX_CONFIG" << 'TOML'

# Venice AI via local proxy
model = "openai-gpt-53-codex"
model_provider = "venice"

[model_providers.venice]
name = "Venice AI"
base_url = "http://localhost:4000/v1"
env_key = "VENICE_PROXY_KEY"
TOML
    echo "  Added Venice provider to config.toml"
fi

# 4. Install LaunchAgent
echo "[3/3] Installing LaunchAgent for auto-start..."
PLIST=~/Library/LaunchAgents/com.venice-codex-proxy.plist
cat > "$PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.venice-codex-proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>${DIR}/venv/bin/python</string>
        <string>${DIR}/proxy.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${DIR}/proxy-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${DIR}/proxy-stderr.log</string>
</dict>
</plist>
PLIST

echo "  Installed LaunchAgent at $PLIST"
echo

echo "=== Setup complete ==="
echo
echo "To start manually:  ./start.sh"
echo "To auto-start:      launchctl load ~/Library/LaunchAgents/com.venice-codex-proxy.plist"
echo "To stop:            launchctl unload ~/Library/LaunchAgents/com.venice-codex-proxy.plist"
echo
echo "Set VENICE_PROXY_KEY to any value (e.g. 'x') in your shell for Codex:"
echo "  export VENICE_PROXY_KEY=x"
