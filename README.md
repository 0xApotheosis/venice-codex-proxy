# Venice Codex Proxy

A lightweight reverse proxy that routes Codex Desktop requests through Venice AI.

## Setup

```bash
git clone git@github.com:0xApotheosis/venice-codex-proxy.git
cd venice-codex-proxy
./setup.sh
```

This creates a Python venv, installs dependencies, prompts for your Venice API key, and configures `~/.codex/config.toml`.

## Start

```bash
./start.sh
```

Verify it's running:

```bash
curl -s http://127.0.0.1:4000/healthz
```

Logs are written to `proxy.log`.

## License

MIT
