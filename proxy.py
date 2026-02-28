#!/usr/bin/env python3
"""
Venice Codex Proxy — transparent reverse proxy for routing OpenAI Codex Desktop
requests through Venice AI.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", str(Path(__file__).parent / "proxy.log"))
LOG_STDERR = os.getenv("LOG_STDERR", "true").strip().lower() in {"1", "true", "yes", "on"}

log = logging.getLogger("venice-proxy")
log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

_fmt = logging.Formatter("%(asctime)s  %(levelname)-5s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

if LOG_STDERR:
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setFormatter(_fmt)
    log.addHandler(_stderr)

try:
    _file = logging.FileHandler(LOG_FILE)
    _file.setFormatter(_fmt)
    log.addHandler(_file)
except OSError:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        parsed = int(val)
    except ValueError:
        log.warning(f"Invalid {name}={val!r}; using default {default}")
        return default
    if parsed < min_value:
        log.warning(f"Invalid {name}={parsed}; using default {default}")
        return default
    return parsed


LISTEN_HOST = os.getenv("PROXY_HOST", "127.0.0.1")
LISTEN_PORT = _env_int("PROXY_PORT", 4000, min_value=1)
VENICE_BASE = os.getenv("VENICE_BASE_URL", "https://api.venice.ai/api/v1")
VENICE_MODEL = os.getenv("VENICE_MODEL", "openai-gpt-53-codex")
REQUEST_PREVIEW_CHARS = _env_int("REQUEST_PREVIEW_CHARS", 80, min_value=0)
MAX_REQUEST_BYTES = _env_int("MAX_REQUEST_BYTES", 10 * 1024 * 1024, min_value=1)
UPSTREAM_TIMEOUT_TOTAL = _env_int("UPSTREAM_TIMEOUT_TOTAL", 300, min_value=1)
UPSTREAM_TIMEOUT_SOCK_READ = _env_int("UPSTREAM_TIMEOUT_SOCK_READ", 120, min_value=1)
UPSTREAM_MAX_CONNECTIONS = _env_int("UPSTREAM_MAX_CONNECTIONS", 100, min_value=1)
LOG_PROMPTS = _env_bool("LOG_PROMPTS", True)


def _looks_like_placeholder(value: str) -> bool:
    low = value.strip().lower()
    return not low or "your-venice-api-key" in low or low in {"changeme", "replace-me"}


def load_api_key() -> str:
    """Load Venice API key from env or .env file."""
    key = os.getenv("VENICE_API_KEY", "").strip()
    if key and not _looks_like_placeholder(key):
        return key

    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "VENICE_API_KEY":
                candidate = v.strip().strip('"').strip("'")
                if candidate and not _looks_like_placeholder(candidate):
                    return candidate

    log.error("VENICE_API_KEY not set. Put it in .env (copy from .env.example) or export it.")
    sys.exit(1)


def _mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]}"


VENICE_API_KEY = load_api_key()

# ---------------------------------------------------------------------------
# HTTP handling via aiohttp
# ---------------------------------------------------------------------------

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    log.error("Missing dependency. Run: pip install -r requirements.txt")
    sys.exit(1)

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=UPSTREAM_MAX_CONNECTIONS),
            timeout=aiohttp.ClientTimeout(
                total=UPSTREAM_TIMEOUT_TOTAL,
                sock_read=UPSTREAM_TIMEOUT_SOCK_READ,
            ),
        )
    return _session


STRIP_REQUEST_HEADERS = {
    "host",
    "authorization",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "upgrade",
}

STRIP_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "content-encoding",
}


def _extract_request_info(body: bytes) -> str:
    """Pull model name and a preview from the request body for logging."""
    try:
        data = json.loads(body)
        model = data.get("model", "?")
        stream = data.get("stream", False)
        tag = "stream" if stream else "sync"

        if not LOG_PROMPTS:
            return f"model={model} [{tag}]"

        preview = ""
        raw_input = data.get("input", [])
        if isinstance(raw_input, str):
            preview = raw_input[:REQUEST_PREVIEW_CHARS]
        elif isinstance(raw_input, list):
            for item in raw_input:
                if not isinstance(item, dict) or item.get("role") != "user":
                    continue
                content = item.get("content", [])
                if isinstance(content, str):
                    preview = content[:REQUEST_PREVIEW_CHARS]
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("text"):
                            preview = str(part["text"])[:REQUEST_PREVIEW_CHARS]
                            break
                break

        return f"model={model} [{tag}] {repr(preview)}" if preview else f"model={model} [{tag}]"
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError):
        return ""


def _build_upstream_url(req: web.Request) -> str:
    """Build upstream URL and strip only the /v1 prefix path component."""
    path = req.path
    if path == "/v1":
        path = ""
    elif path.startswith("/v1/"):
        path = path[3:]

    upstream_url = f"{VENICE_BASE.rstrip('/')}{path}"
    if req.query_string:
        upstream_url += f"?{req.query_string}"
    return upstream_url


def _request_too_large(req: web.Request) -> bool:
    return req.content_length is not None and req.content_length > MAX_REQUEST_BYTES


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


def _normalize_content_part_for_venice(part: dict) -> tuple[dict, bool]:
    """
    Normalize OpenAI Responses content-part variants to the format Venice expects.
    """
    changed = False
    out = dict(part)

    part_type = out.get("type")
    if part_type == "input_text":
        out["type"] = "text"
        changed = True
    elif part_type == "input_image":
        out["type"] = "image_url"
        changed = True

    image_url = out.get("image_url")
    if isinstance(image_url, str):
        out["image_url"] = {"url": image_url}
        changed = True

    return out, changed


def _normalize_input_for_venice(data: dict) -> tuple[dict, bool]:
    """Normalize request body fields for Venice compatibility."""
    raw_input = data.get("input")
    if not isinstance(raw_input, list):
        return data, False

    changed = False
    normalized_items = []

    for item in raw_input:
        if not isinstance(item, dict):
            normalized_items.append(item)
            continue

        content = item.get("content")
        if not isinstance(content, list):
            normalized_items.append(item)
            continue

        item_changed = False
        normalized_content = []
        for part in content:
            if isinstance(part, dict):
                normalized_part, part_changed = _normalize_content_part_for_venice(part)
                normalized_content.append(normalized_part)
                item_changed = item_changed or part_changed
            else:
                normalized_content.append(part)

        if item_changed:
            normalized_item = dict(item)
            normalized_item["content"] = normalized_content
            normalized_items.append(normalized_item)
            changed = True
        else:
            normalized_items.append(item)

    if not changed:
        return data, False

    normalized = dict(data)
    normalized["input"] = normalized_items
    return normalized, True


async def handle_request(req: web.Request) -> web.StreamResponse:
    """Forward any request to Venice, swapping auth."""
    t0 = time.monotonic()
    req_id = req.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    prefix = f"[{req_id}]"

    if _request_too_large(req):
        log.warning(f"{prefix} <- 413 request too large ({req.content_length} bytes)")
        return web.json_response(
            {
                "error": {
                    "message": f"Request exceeds MAX_REQUEST_BYTES ({MAX_REQUEST_BYTES})",
                    "type": "request_too_large",
                }
            },
            status=413,
            headers={"x-request-id": req_id},
        )

    upstream_url = _build_upstream_url(req)

    headers = {}
    for k, v in req.headers.items():
        if k.lower() not in STRIP_REQUEST_HEADERS:
            headers[k] = v
    headers["Authorization"] = f"Bearer {VENICE_API_KEY}"
    headers["X-Request-Id"] = req_id

    body = await req.read()
    if len(body) > MAX_REQUEST_BYTES:
        log.warning(f"{prefix} <- 413 request too large ({len(body)} bytes)")
        return web.json_response(
            {
                "error": {
                    "message": f"Request exceeds MAX_REQUEST_BYTES ({MAX_REQUEST_BYTES})",
                    "type": "request_too_large",
                }
            },
            status=413,
            headers={"x-request-id": req_id},
        )

    if body:
        try:
            data = json.loads(body)
            body_changed = False

            data, normalized = _normalize_input_for_venice(data)
            if normalized:
                body_changed = True

            original_model = data.get("model", "")
            if original_model != VENICE_MODEL:
                data["model"] = VENICE_MODEL
                body_changed = True
                log.info(f"{prefix}    model rewrite: {original_model} -> {VENICE_MODEL}")

            if body_changed:
                body = json.dumps(data).encode()

            if normalized:
                log.info(f"{prefix}    normalized input content for Venice image/text compatibility")
        except (json.JSONDecodeError, KeyError):
            pass

    req_info = _extract_request_info(body)
    log.info(f"{prefix} -> {req.method} {req.path}  {req_info}")

    session = await get_session()
    try:
        async with session.request(method=req.method, url=upstream_url, headers=headers, data=body) as upstream_resp:
            ct = upstream_resp.headers.get("content-type", "")
            is_stream = "text/event-stream" in ct or "stream" in ct

            if is_stream:
                resp = web.StreamResponse(
                    status=upstream_resp.status,
                    headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in STRIP_RESPONSE_HEADERS},
                )
                resp.headers["x-request-id"] = req_id
                await resp.prepare(req)

                bytes_streamed = 0
                tail_data = b""
                try:
                    async for chunk in upstream_resp.content.iter_any():
                        await resp.write(chunk)
                        bytes_streamed += len(chunk)
                        tail_data = (tail_data + chunk)[-65536:]
                    await resp.write_eof()
                except ConnectionResetError:
                    elapsed = time.monotonic() - t0
                    log.info(f"{prefix} <- client disconnected after {bytes_streamed:,} bytes / {elapsed:.1f}s")
                    return resp

                elapsed = time.monotonic() - t0
                usage_info = ""
                try:
                    text = tail_data.decode("utf-8", errors="ignore")
                    for line in reversed(text.splitlines()):
                        if line.startswith("data: ") and "usage" in line:
                            event_data = json.loads(line[6:])
                            usage = event_data.get("response", {}).get("usage") or event_data.get("usage")
                            if usage:
                                inp = usage.get("input_tokens", 0)
                                out = usage.get("output_tokens", 0)
                                cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
                                usage_info = f"  tokens: {inp} in ({cached} cached) / {out} out"
                            break
                except Exception:
                    pass

                log.info(f"{prefix} <- {upstream_resp.status} streamed {bytes_streamed:,} bytes in {elapsed:.1f}s{usage_info}")
                return resp

            resp_body = await upstream_resp.read()
            elapsed = time.monotonic() - t0
            resp_info = _extract_response_info(resp_body)

            resp = web.Response(
                status=upstream_resp.status,
                body=resp_body,
                headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in STRIP_RESPONSE_HEADERS},
            )
            resp.headers["x-request-id"] = req_id

            log.info(f"{prefix} <- {upstream_resp.status} {len(resp_body):,} bytes in {elapsed:.1f}s  {resp_info}")

            if upstream_resp.status >= 400:
                log.warning(f"{prefix}    upstream error body: {resp_body[:500]}")

            return resp

    except (ConnectionResetError, OSError) as e:
        elapsed = time.monotonic() - t0
        log.info(f"{prefix} <- client disconnected after {elapsed:.1f}s: {e}")
        return web.Response(status=499, headers={"x-request-id": req_id})
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        log.error(f"{prefix} <- PROXY TIMEOUT after {elapsed:.1f}s")
        return web.json_response(
            {"error": {"message": "Upstream timeout", "type": "proxy_timeout"}},
            status=504,
            headers={"x-request-id": req_id},
        )
    except aiohttp.ClientError as e:
        elapsed = time.monotonic() - t0
        log.error(f"{prefix} <- PROXY ERROR after {elapsed:.1f}s: {e}")
        return web.json_response(
            {"error": {"message": str(e), "type": "proxy_error"}},
            status=502,
            headers={"x-request-id": req_id},
        )


async def healthz(_req: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "service": "venice-codex-proxy",
            "model": VENICE_MODEL,
            "upstream": VENICE_BASE,
        }
    )


async def index(_req: web.Request) -> web.Response:
    return web.json_response(
        {
            "service": "venice-codex-proxy",
            "status": "ok",
            "health": "/healthz",
            "ready": "/readyz",
            "proxy": "/v1/*",
        }
    )


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


def create_app() -> web.Application:
    # aiohttp defaults to a 1 MiB max request body, which is too small for
    # larger Codex payloads (tool output, long prompts, images). Keep this
    # aligned with MAX_REQUEST_BYTES so our explicit 413 handling is used.
    app = web.Application(client_max_size=MAX_REQUEST_BYTES)
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/readyz", healthz)
    app.router.add_route("*", "/{path:.*}", handle_request)
    return app


async def cleanup(_app: web.Application):
    global _session
    if _session and not _session.closed:
        await _session.close()


def main():
    log.info("Venice Codex Proxy starting")
    log.info(f"  Listening: http://{LISTEN_HOST}:{LISTEN_PORT}")
    log.info(f"  Upstream:  {VENICE_BASE}")
    log.info(f"  Model:     {VENICE_MODEL}")
    log.info(f"  API Key:   {_mask_secret(VENICE_API_KEY)}")
    log.info(f"  Log file:  {LOG_FILE}")
    log.info(f"  STDERR:    {'on' if LOG_STDERR else 'off'}")
    log.info(f"  Max req:   {MAX_REQUEST_BYTES:,} bytes")
    log.info(f"  Timeouts:  total={UPSTREAM_TIMEOUT_TOTAL}s sock_read={UPSTREAM_TIMEOUT_SOCK_READ}s")
    log.info(f"  Prompt log:{'on' if LOG_PROMPTS else 'off'}")
    log.info("")

    app = create_app()
    app.on_cleanup.append(cleanup)

    try:
        web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT, print=None)
    except OSError as e:
        log.error(f"Failed to bind http://{LISTEN_HOST}:{LISTEN_PORT}: {e}")
        log.error("Tip: stop the existing process or change PROXY_PORT in .env")
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
