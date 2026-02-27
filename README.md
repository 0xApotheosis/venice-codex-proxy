# Venice Codex Proxy

A lightweight transparent reverse proxy that routes [OpenAI Codex](https://openai.com/index/codex/) desktop requests through [Venice AI](https://venice.ai).

Venice natively supports the `/v1/responses` API. This proxy stays mostly transparent, while applying a small compatibility normalization for Codex desktop multimodal payloads. It:

1. Accepts requests on `localhost:4000`
2. Normalizes Codex content parts for Venice compatibility (`input_text` → `text`, `input_image` → `image_url`, and string `image_url` → `{"url": ...}`)
3. Rewrites the model name to `openai-gpt-53-codex`
4. Swaps the auth header for your Venice API key
5. Forwards to `https://api.venice.ai/api/v1`
6. Streams the response back byte-for-byte

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
| `LOG_STDERR` | `true` | Mirror logs to terminal/stderr (`false` for file-only logs) |
| `LOG_PROMPTS` | `true` | Include user prompt previews in logs (`false` to disable) |
| `REQUEST_PREVIEW_CHARS` | `80` | Max chars to include in request preview logs |
| `MAX_REQUEST_BYTES` | `10485760` | Max request payload size before returning `413` |
| `UPSTREAM_TIMEOUT_TOTAL` | `300` | Total upstream request timeout (seconds) |
| `UPSTREAM_TIMEOUT_SOCK_READ` | `120` | Upstream socket-read timeout (seconds) |
| `UPSTREAM_MAX_CONNECTIONS` | `100` | Max pooled upstream connections |

## Logging

Logs go to both stderr (visible in terminal) and `proxy.log` (always available).

```
2026-02-27 15:23:07  INFO   -> POST /v1/responses  model=openai-gpt-53-codex [stream] 'What is 2+2?'
2026-02-27 15:23:07  INFO      model rewrite: gpt-5.1-codex-mini -> openai-gpt-53-codex
2026-02-27 15:23:09  INFO   <- 200 streamed 2,728 bytes in 2.1s  tokens: 1402 in (1280 cached) / 55 out
```

Each request logs:
- Request ID, timestamp, method, and path
- Model name (original and rewritten if different)
- Stream vs sync mode
- Optional preview of the user's input (`LOG_PROMPTS`)
- Response status, bytes, elapsed time, and token usage

Every proxied response includes `x-request-id` to correlate client errors with proxy logs.

Tail the log in real time:

### Disable terminal logs

Set this in your shell (or LaunchAgent env) before starting:

```bash
export LOG_STDERR=false
./start.sh
```

With `LOG_STDERR=false`, logs still go to `proxy.log`, but not to terminal stderr.

```bash
tail -f proxy.log
```

## Health Endpoints

```bash
# Liveness
curl -s http://127.0.0.1:4000/healthz

# Readiness (same payload)
curl -s http://127.0.0.1:4000/readyz
```

Both return JSON with service status, upstream base URL, and configured model.

## Tests

Run unit tests with:

```bash
VENICE_API_KEY=test-key ./venv/bin/python -m unittest -v test_proxy_normalization.py
```

The test suite validates request normalization for multimodal content and no-op behavior for unsupported input shapes.

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

Codex desktop sends all requests to the OpenAI `/v1/responses` API. Venice AI supports this endpoint natively for their OpenAI-proxied models.

To handle current Codex desktop multimodal payload variants, the proxy performs a narrow compatibility normalization before forwarding:
- `content[].type: input_text` becomes `text`
- `content[].type: input_image` becomes `image_url`
- `content[].image_url` string becomes an object: `{"url": ...}`

The proxy also rewrites **all** model names to the configured Venice model (`openai-gpt-53-codex` by default). This handles Codex's sub-agent requests (e.g. `gpt-5.1-codex-mini`) that Venice wouldn't recognize.

## License

MIT
