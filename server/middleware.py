"""Cross-cutting HTTP middleware: request IDs, structured logging,
optional API key auth, and simple per-key rate limiting.

All behavior is opt-in via env vars:
- API_KEYS="key1,key2,..." enables auth. Unset → open server (dev default).
- RATE_LIMIT_PER_MIN=60    enables rate limiting. Unset → no limiting.
- LOG_FORMAT=json          emits line-per-record JSON. Default = text.

Request IDs are always on. Each request gets a UUID attached to its
log records and echoed in the `X-Request-ID` response header. Clients
can supply their own via the `X-Request-ID` request header (handy for
correlating with upstream logs); if they don't, we generate one.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from collections import deque
from contextvars import ContextVar
from typing import Any, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

# Context var so log records can pick up the current request ID
# without it having to be passed through every function signature.
_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)

log = logging.getLogger("a11y_audit")

MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject oversized fixed-length and streamed request bodies."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    response = JSONResponse(
                        {"detail": "Request body too large"}, status_code=413
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                # The HTTP server normally rejects malformed Content-Length.
                pass

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            response = JSONResponse(
                {"detail": "Request body too large"}, status_code=413
            )
            await response(scope, receive, send)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """Line-per-record JSON. Includes the request_id when set."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Idempotent logging setup. Call once at app startup."""
    root = logging.getLogger()
    if getattr(root, "_a11y_audit_configured", False):
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    # Remove existing handlers so we own the stream.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())

    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
            )
        )
    root.addHandler(handler)
    root._a11y_audit_configured = True  # type: ignore[attr-defined]


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to every request; expose via X-Request-ID."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        supplied = request.headers.get("x-request-id", "")
        rid = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
        token = _REQUEST_ID.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            log.info(
                "%s %s -> %s (%.1f ms)",
                request.method,
                request.url.path,
                getattr(locals().get("response"), "status_code", "ERR"),
                duration_ms,
            )
            _REQUEST_ID.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


# ---------------------------------------------------------------------------
# API key authentication


def _api_keys_from_env() -> set[str]:
    raw = os.environ.get("API_KEYS", "").strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


# Paths exempt from auth even when API_KEYS is set. /docs, /openapi.json
# and /health stay open so clients can discover / probe without a key.
_PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Enforce an API key when ``API_KEYS`` is set.

    Keys are supplied via either the ``X-API-Key`` header or a
    ``Authorization: Bearer <key>`` header.
    """

    def __init__(self, app):
        super().__init__(app)
        self._keys = _api_keys_from_env()
        self._enabled = bool(self._keys)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._enabled or request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        supplied = request.headers.get("x-api-key")
        if not supplied:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()

        matched = next(
            (key for key in self._keys if hmac.compare_digest(supplied or "", key)),
            None,
        )
        if matched is None:
            # Keep the error body neutral — don't leak whether auth is enabled
            # or whether the key was absent vs wrong.
            return JSONResponse(
                {"detail": "Invalid or missing API key"}, status_code=401
            )

        # Stash the key ID (first 8 chars) so rate limiter can key off it.
        request.state.api_key_id = hashlib.sha256(matched.encode("utf-8")).hexdigest()[:16]
        return await call_next(request)


# ---------------------------------------------------------------------------
# Simple in-process rate limiter (token-per-minute window).
#
# Good enough for single-process deployments. For multi-process / multi-host,
# swap the in-memory deque for a Redis-backed counter.


class RateLimitMiddleware(BaseHTTPMiddleware):
    # Cap on unique buckets we'll track. Prevents an attacker cycling
    # through IPs from blowing up memory. When we'd exceed the cap we
    # drop the least-recently-used bucket; the guarantee is soft (a
    # blacklisted caller returns briefly on eviction), acceptable for a
    # single-process in-memory limiter.
    _MAX_BUCKETS = 10_000

    def __init__(self, app):
        super().__init__(app)
        self._limit = int(os.environ.get("RATE_LIMIT_PER_MIN", "0"))
        self._enabled = self._limit > 0
        self._window_s = 60.0
        # OrderedDict so we can evict LRU in O(1).
        from collections import OrderedDict

        self._hits: "OrderedDict[str, deque[float]]" = OrderedDict()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._enabled or request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        bucket = getattr(request.state, "api_key_id", None) or (
            request.client.host if request.client else "anon"
        )
        now = time.monotonic()

        hits = self._hits.get(bucket)
        if hits is None:
            hits = deque()
            self._hits[bucket] = hits
            if len(self._hits) > self._MAX_BUCKETS:
                # popitem(last=False) → evict oldest.
                self._hits.popitem(last=False)
        else:
            # Touch LRU position.
            self._hits.move_to_end(bucket)

        cutoff = now - self._window_s
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            retry_after = max(1, int(self._window_s - (now - hits[0])))
            resp = Response(
                content=json.dumps({"detail": "Rate limit exceeded"}),
                status_code=429,
                media_type="application/json",
            )
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        hits.append(now)
        return await call_next(request)
