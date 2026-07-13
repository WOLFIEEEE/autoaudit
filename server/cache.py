"""Optional Redis cache for recent audit results.

Gracefully degrades when Redis is unavailable — the server still works,
it just doesn't cache. That means /audit/quick and /health can be used
locally without spinning up Redis.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from server.config import CONFIG

log = logging.getLogger(__name__)

# Safety cap on cached payload size (same rationale as database.py).
_MAX_CACHE_JSON_BYTES = 16 * 1024 * 1024

try:
    import redis  # type: ignore

    _client: "redis.Redis | None" = redis.Redis.from_url(
        CONFIG.redis_url,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        decode_responses=True,
    )
    if not CONFIG.cache_enabled:
        _client = None
except Exception as exc:  # pragma: no cover - import-time guard
    log.warning(
        "Redis unavailable (%s: %s); caching disabled", type(exc).__name__, exc
    )
    _client = None


_SENSITIVE_OPTION_KEYS = frozenset(
    {"basic_auth", "cookies", "headers", "login", "openrouter_api_key"}
)


def _is_cacheable(options: dict[str, Any] | None) -> bool:
    """Authenticated/personalized audits must never enter the shared cache."""
    return not any((options or {}).get(key) for key in _SENSITIVE_OPTION_KEYS)


def _url_key(url: str, options: dict[str, Any] | None = None) -> str:
    """Return a deterministic key for every input that can change a result.

    The old URL-only key mixed target levels, viewports, module settings, and
    even authenticated and anonymous audits.  The canonical JSON is hashed, so
    option values are not exposed in Redis keys.
    """
    canonical = json.dumps(
        {"url": url, "options": options or {}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"a11y:audit:v2:{digest}"


def get_cached_result(
    url: str, options: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    if _client is None or not _is_cacheable(options):
        return None
    key = _url_key(url, options)
    try:
        raw = _client.get(key)
    except Exception as exc:
        log.debug("Redis get failed: %s", exc)
        return None
    if not raw:
        return None
    if len(raw.encode("utf-8")) > _MAX_CACHE_JSON_BYTES:
        log.warning("cached result exceeds size cap; discarding")
        try:
            _client.delete(key)
        except Exception:
            pass
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_cached_result(
    url: str,
    payload: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> None:
    if _client is None or not _is_cacheable(options):
        return
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        log.debug("refusing to cache non-serializable payload: %s", exc)
        return
    if len(encoded.encode("utf-8")) > _MAX_CACHE_JSON_BYTES:
        log.warning("audit result exceeds cache size cap; skipping set")
        return
    try:
        _client.setex(_url_key(url, options), CONFIG.cache_ttl_seconds, encoded)
    except Exception as exc:
        log.debug("Redis set failed: %s", exc)


def delete_cached_result(url: str, options: dict[str, Any] | None = None) -> None:
    if _client is None or not _is_cacheable(options):
        return
    try:
        _client.delete(_url_key(url, options))
    except Exception as exc:
        log.debug("Redis delete failed: %s", exc)


def ping() -> bool:
    """Quick health probe: can we reach Redis?

    Returns True when the server responded to PING, False if Redis was
    never configured or the ping failed.
    """
    if _client is None:
        return False
    try:
        return bool(_client.ping())
    except Exception as exc:
        log.debug("Redis ping failed: %s", exc)
        return False
