#!/usr/bin/env python3
"""
Venice Codex Proxy — transparent reverse proxy for routing OpenAI Codex desktop
requests through Venice AI.

Venice natively supports the /v1/responses API, so this proxy simply:
  1. Accepts any request on localhost:4000
  2. Swaps the auth header for the Venice API key
  3. Forwards to https://api.venice.ai/api/v1
  4. Streams the response back byte-for-byte

No format translation needed. Dead simple.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", str(Path(__file__).parent / "proxy.log"))

log = logging.getLogger("venice-proxy")
log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

_fmt = logging.Formatter(
    "%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Always log to stderr (visible when running in foreground)
_stderr = logging.StreamHandler(sys.stderr)
_stderr.setFormatter(_fmt)
log.addHandler(_stderr)

# Also log to file (visible when running via LaunchAgent)
try:
    _file = logging.FileHandler(LOG_FILE)
    _file.setFormatter(_fmt)
    log.addHandler(_file)
except OSError:
    pass  # Can't write log file — stderr only is fine

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LISTEN_HOST = os.getenv("PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("PROXY_PORT", "4000"))
VENICE_BASE = os.getenv("VENICE_BASE_URL", "https://api.venice.ai/api/v1")
VENICE_MODEL = os.getenv("VENICE_MODEL", "openai-gpt-53-codex")

def load_api_key() -> str:
    """Load Venice API key from env or .env file."""
    key = os.getenv("VENICE_API_KEY")
    if key:
        return key
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("VENICE_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    log.error("VENICE_API_KEY not set. Put it in .env or export it.")
    sys.exit(1)

VENICE_API_KEY = load_api_key()

# ---------------------------------------------------------------------------
# HTTP handling via aiohttp
# ---------------------------------------------------------------------------

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    log.error("Missing dependency. Run: pip install aiohttp")
    sys.exit(1)

# Shared client session (connection pooling)
_session: aiohttp.ClientSession | None = None

async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=300, sock_read=120)
        )
    return _session

# Headers to strip from the forwarded request (hop-by-hop or irrelevant)
STRIP_REQUEST_HEADERS = {
    "host", "authorization", "content-length", "transfer-encoding",
    "connection", "keep-alive", "upgrade",
}

# Headers to strip from the upstream response
STRIP_RESPONSE_HEADERS = {
    "content-length", "transfer-encoding", "connection", "keep-alive",
    "content-encoding",
}

def _extract_request_info(body: bytes) -> str:
    """Pull model name and a preview from the request body for logging."""
    try:
        data = json.loads(body)
        model = data.get("model", "?")
        stream = data.get("stream", False)
        # Try to get a snippet of the user's input
        preview = ""
        for item in data.get("input", []):
            if item.get("role") == "user":
                content = item.get("content", [])
                if isinstance(content, str):
                    preview = content[:80]
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("text"):
                            preview = part["text"][:80]
                            break
                break
        tag = "stream" if stream else "sync"
        return f"model={model} [{tag}] {repr(preview)}" if preview else f"model={model} [{tag}]"
    except (json.JSONDecodeError, KeyError):
        return ""

def _extract_response_info(body: bytes) -> str:
    """Pull token usage from a non-streaming response body."""
    try:
        data = json.loads(body)
        usage = data.get("usage", {})
        if usage:
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
            return f"tokens: {inp} in ({cached} cached) / {out} out"
        status = data.get("status", "")
        return f"status={status}" if status else ""
    except (json.JSONDecodeError, KeyError):
        return ""

async def handle_request(req: web.Request) -> web.StreamResponse:
    """Forward any request to Venice, swapping auth."""
    t0 = time.monotonic()

    # Build upstream URL
    path = req.path  # e.g. /v1/responses
    # Strip /v1 prefix since VENICE_BASE already includes it
    if path.startswith("/v1"):
        path = path[3:]  # /v1/responses -> /responses
    upstream_url = f"{VENICE_BASE.rstrip('/')}{path}"
    if req.query_string:
        upstream_url += f"?{req.query_string}"

    # Build headers — swap auth, pass everything else through
    headers = {}
    for k, v in req.headers.items():
        if k.lower() not in STRIP_REQUEST_HEADERS:
            headers[k] = v
    headers["Authorization"] = f"Bearer {VENICE_API_KEY}"

    # Read request body, rewrite model name to Venice target
    body = await req.read()
    if body:
        try:
            data = json.loads(body)
            original_model = data.get("model", "")
            if original_model != VENICE_MODEL:
                data["model"] = VENICE_MODEL
                body = json.dumps(data).encode()
                log.info(f"   model rewrite: {original_model} -> {VENICE_MODEL}")
        except (json.JSONDecodeError, KeyError):
            pass
    req_info = _extract_request_info(body)

    log.info(f"-> {req.method} {req.path}  {req_info}")

    session = await get_session()
    try:
        async with session.request(
            method=req.method,
            url=upstream_url,
            headers=headers,
            data=body,
        ) as upstream_resp:
            # Check if this is a streaming response
            ct = upstream_resp.headers.get("content-type", "")
            is_stream = "text/event-stream" in ct or "stream" in ct

            if is_stream:
                resp = web.StreamResponse(
                    status=upstream_resp.status,
                    headers={
                        k: v for k, v in upstream_resp.headers.items()
                        if k.lower() not in STRIP_RESPONSE_HEADERS
                    },
                )
                await resp.prepare(req)

                bytes_streamed = 0
                last_data = b""
                try:
                    async for chunk in upstream_resp.content.iter_any():
                        await resp.write(chunk)
                        bytes_streamed += len(chunk)
                        last_data = chunk  # Keep last chunk for token info
                    await resp.write_eof()
                except ConnectionResetError:
                    elapsed = time.monotonic() - t0
                    log.info(f"<- client disconnected after {bytes_streamed:,} bytes / {elapsed:.1f}s")
                    return resp
                elapsed = time.monotonic() - t0

                # Try to extract usage from the final "response.completed" SSE event
                usage_info = ""
                try:
                    text = last_data.decode("utf-8", errors="ignore")
                    for line in reversed(text.splitlines()):
                        if line.startswith("data: ") and "usage" in line:
                            event_data = json.loads(line[6:])
                            usage = (
                                event_data.get("response", {}).get("usage")
                                or event_data.get("usage")
                            )
                            if usage:
                                inp = usage.get("input_tokens", 0)
                                out = usage.get("output_tokens", 0)
                                cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
                                usage_info = f"  tokens: {inp} in ({cached} cached) / {out} out"
                            break
                except Exception:
                    pass

                log.info(
                    f"<- {upstream_resp.status} streamed {bytes_streamed:,} bytes "
                    f"in {elapsed:.1f}s{usage_info}"
                )
                return resp
            else:
                resp_body = await upstream_resp.read()
                elapsed = time.monotonic() - t0
                resp_info = _extract_response_info(resp_body)

                resp = web.Response(
                    status=upstream_resp.status,
                    body=resp_body,
                    headers={
                        k: v for k, v in upstream_resp.headers.items()
                        if k.lower() not in STRIP_RESPONSE_HEADERS
                    },
                )

                log.info(f"<- {upstream_resp.status} {len(resp_body):,} bytes in {elapsed:.1f}s  {resp_info}")

                if upstream_resp.status >= 400:
                    log.warning(f"   upstream error body: {resp_body[:500]}")

                return resp

    except (ConnectionResetError, OSError) as e:
        elapsed = time.monotonic() - t0
        log.info(f"<- client disconnected after {elapsed:.1f}s: {e}")
        return web.Response(status=499)  # client closed
    except aiohttp.ClientError as e:
        elapsed = time.monotonic() - t0
        log.error(f"<- PROXY ERROR after {elapsed:.1f}s: {e}")
        return web.json_response(
            {"error": {"message": str(e), "type": "proxy_error"}},
            status=502,
        )

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    # Catch-all route — forwards everything
    app.router.add_route("*", "/{path:.*}", handle_request)
    return app

async def cleanup(app: web.Application):
    global _session
    if _session and not _session.closed:
        await _session.close()

def main():
    log.info("Venice Codex Proxy starting")
    log.info(f"  Listening: http://{LISTEN_HOST}:{LISTEN_PORT}")
    log.info(f"  Upstream:  {VENICE_BASE}")
    log.info(f"  Model:     {VENICE_MODEL}")
    log.info(f"  API Key:   {VENICE_API_KEY[:8]}...{VENICE_API_KEY[-4:]}")
    log.info(f"  Log file:  {LOG_FILE}")
    log.info("")

    app = create_app()
    app.on_cleanup.append(cleanup)
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT, print=None)

if __name__ == "__main__":
    main()
