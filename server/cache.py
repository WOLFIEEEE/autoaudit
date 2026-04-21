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
        CONFIG.redis_url, socket_connect_timeout=1, decode_responses=True
    )
except Exception as exc:  # pragma: no cover - import-time guard
    log.warning("Redis unavailable, caching disabled: %s", exc)
    _client = None


def _url_key(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"a11y:audit:url:{digest}"


def get_cached_result(url: str) -> dict[str, Any] | None:
    if _client is None:
        return None
    try:
        raw = _client.get(_url_key(url))
    except Exception as exc:
        log.debug("Redis get failed: %s", exc)
        return None
    if not raw:
        return None
    if len(raw.encode("utf-8")) > _MAX_CACHE_JSON_BYTES:
        log.warning("cached result exceeds size cap; discarding")
        try:
            _client.delete(_url_key(url))
        except Exception:
            pass
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_cached_result(url: str, payload: dict[str, Any]) -> None:
    if _client is None:
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
        _client.setex(_url_key(url), CONFIG.cache_ttl_seconds, encoded)
    except Exception as exc:
        log.debug("Redis set failed: %s", exc)


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
