# Venice Codex Proxy

A lightweight transparent reverse proxy that routes Codex Desktop requests through Venice AI.

It stays mostly transparent while adding a narrow compatibility normalization for Codex multimodal payloads and rewriting model names to a Venice-compatible model.

## 60-Second Setup (Recommended)

```bash
# 1) Clone your fork
cd /path/where/you/want/it
# git clone <your-fork-url>
cd venice-codex-proxy

# 2) One-time setup (prompts for API key if needed)
./setup.sh

# 3) Verify proxy health
curl -s http://127.0.0.1:4000/healthz
```

That’s it. `./setup.sh` will:
- Create/update `venv` and install dependencies from `requirements.txt`
- Ensure your Venice API key is set in `.env`
- Add/update a managed Venice block in `~/.codex/config.toml`
- Install and start a macOS LaunchAgent (unless `--no-agent`)

## Manual Foreground Start

```bash
cp .env.example .env
# edit .env and set VENICE_API_KEY
./start.sh
```

## `setup.sh` Options

```bash
./setup.sh --help
./setup.sh --api-key <your-key>
./setup.sh --no-agent
./setup.sh --no-start
```

Notes:
- Re-running `./setup.sh` is safe and idempotent.
- It updates only a managed block in `~/.codex/config.toml` marked by:
  - `# >>> venice-codex-proxy >>>`
  - `# <<< venice-codex-proxy <<<`

## Codex Desktop Config (Managed Block)

`setup.sh` writes this block:

```toml
# >>> venice-codex-proxy >>>
model = "openai-gpt-53-codex"
model_provider = "venice"

[model_providers.venice]
name = "Venice AI"
base_url = "http://127.0.0.1:4000/v1"
experimental_bearer_token = "x"
# <<< venice-codex-proxy <<<
```

The bearer token is intentionally a dummy value; the proxy injects your real Venice key from `.env`.

## Managing the Proxy

```bash
# Foreground run
./start.sh

# Health
curl -s http://127.0.0.1:4000/
curl -s http://127.0.0.1:4000/healthz
curl -s http://127.0.0.1:4000/readyz

# Logs
tail -f proxy.log

# Is anything listening on 4000?
lsof -i :4000
```

LaunchAgent (macOS):

```bash
# Restart service
launchctl kickstart -k gui/$(id -u)/com.venice-codex-proxy

# Stop service
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.venice-codex-proxy.plist

# Start service
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.venice-codex-proxy.plist
```

## Environment Variables

All optional except `VENICE_API_KEY`.

| Variable | Default | Description |
|---|---|---|
| `VENICE_API_KEY` | (from `.env`) | Your Venice API key |
| `VENICE_MODEL` | `openai-gpt-53-codex` | Upstream Venice model |
| `VENICE_BASE_URL` | `https://api.venice.ai/api/v1` | Venice API base URL |
| `PROXY_HOST` | `127.0.0.1` | Listen host |
| `PROXY_PORT` | `4000` | Listen port |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FILE` | `./proxy.log` | File log path |
| `LOG_STDERR` | `true` | Mirror logs to stderr |
| `LOG_PROMPTS` | `true` | Include prompt previews in logs |
| `REQUEST_PREVIEW_CHARS` | `80` | Max chars in prompt preview |
| `MAX_REQUEST_BYTES` | `10485760` | Max payload size before `413` |
| `UPSTREAM_TIMEOUT_TOTAL` | `300` | Upstream total timeout (sec) |
| `UPSTREAM_TIMEOUT_SOCK_READ` | `120` | Upstream socket read timeout (sec) |
| `UPSTREAM_MAX_CONNECTIONS` | `100` | Upstream connection pool size |

## Tests

```bash
VENICE_API_KEY=test-key ./venv/bin/python -m unittest -v test_proxy_normalization.py
```

## CI

GitHub Actions runs tests on Python 3.11 and 3.12.

## License

MIT
