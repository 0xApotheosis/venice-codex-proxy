# Venice Codex Proxy

A lightweight transparent reverse proxy that routes [OpenAI Codex](https://openai.com/index/codex/) desktop requests through [Venice AI](https://venice.ai).

Venice natively supports the `/v1/responses` API, so this proxy doesn't do any format translation. It simply:

1. Accepts requests on `localhost:4000`
2. Rewrites the model name to `openai-gpt-53-codex`
3. Swaps the auth header for your Venice API key
4. Forwards to `https://api.venice.ai/api/v1`
5. Streams the response back byte-for-byte

Single file. One dependency (`aiohttp`). No framework overhead.

## Quick Start

```bash
# Clone
git clone git@github.com:0xApotheosis/venice-codex-proxy.git
cd venice-codex-proxy

# Add your Venice API key
cp .env.example .env
# Edit .env and paste your key

# Start
./start.sh
```

The first run creates a Python venv and installs `aiohttp` automatically.

## Full Setup (Codex config + auto-start)

```bash
./setup.sh
```

This will:
- Create the Python venv and install dependencies
- Add a Venice provider to `~/.codex/config.toml`
- Install a macOS LaunchAgent for auto-start on login

## Codex Desktop Configuration

The proxy expects this in `~/.codex/config.toml`:

```toml
model = "openai-gpt-53-codex"
model_provider = "venice"

[model_providers.venice]
name = "Venice AI"
base_url = "http://localhost:4000/v1"
experimental_bearer_token = "x"
```

The `experimental_bearer_token` is a dummy value — the proxy handles the real Venice API key from its `.env` file.

## Environment Variables

All optional. Defaults are tuned for typical use.

| Variable | Default | Description |
|---|---|---|
| `VENICE_API_KEY` | (from `.env` file) | Your Venice API key |
| `VENICE_MODEL` | `openai-gpt-53-codex` | Target model on Venice |
| `VENICE_BASE_URL` | `https://api.venice.ai/api/v1` | Venice API base URL |
| `PROXY_HOST` | `127.0.0.1` | Listen address |
| `PROXY_PORT` | `4000` | Listen port |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FILE` | `./proxy.log` | Log file path |

## Logging

Logs go to both stderr (visible in terminal) and `proxy.log` (always available).

```
2026-02-27 15:23:07  INFO   -> POST /v1/responses  model=openai-gpt-53-codex [stream] 'What is 2+2?'
2026-02-27 15:23:07  INFO      model rewrite: gpt-5.1-codex-mini -> openai-gpt-53-codex
2026-02-27 15:23:09  INFO   <- 200 streamed 2,728 bytes in 2.1s  tokens: 1402 in (1280 cached) / 55 out
```

Each request logs:
- Timestamp, method, and path
- Model name (original and rewritten if different)
- Stream vs sync mode
- Preview of the user's input
- Response status, bytes, elapsed time, and token usage

Tail the log in real time:

```bash
tail -f proxy.log
```

## Managing the Proxy

```bash
# Start manually
./start.sh

# Auto-start on login (after running setup.sh)
launchctl load ~/Library/LaunchAgents/com.venice-codex-proxy.plist

# Stop auto-start
launchctl unload ~/Library/LaunchAgents/com.venice-codex-proxy.plist

# Check if running
lsof -i :4000
```

## How It Works

Codex desktop sends all requests to the OpenAI `/v1/responses` API. Venice AI supports this endpoint natively for their OpenAI-proxied models, so no request/response format translation is needed.

The proxy rewrites **all** model names to the configured Venice model (`openai-gpt-53-codex` by default). This handles Codex's sub-agent requests (e.g. `gpt-5.1-codex-mini`) that Venice wouldn't recognize.

## License

MIT
