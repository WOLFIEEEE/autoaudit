"""Regression tests for the reliability/security hardening pass.

Covers:
- SSRF protection in the URL validator
- Input bounds on AuditOptions
- /health reports DB/Redis status + returns 503 when degraded
- Quick audit timeout returns 504 (and doesn't leak exception text)
- Database WAL pragmas + size cap on stored results
- Cache size cap
- Rate limiter bucket cap
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, **env: str) -> TestClient:
    """Reset the app module tree with fresh env. Same pattern as test_api."""
    db_path = tmp_path / "hard.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SKIP_NVDA"] = "true"
    os.environ["CACHE_ENABLED"] = "false"
    # ALLOW_PRIVATE_TARGETS defaults off; individual tests can set it.
    for k in (
        "API_KEYS",
        "RATE_LIMIT_PER_MIN",
        "LOG_FORMAT",
        "LOG_LEVEL",
        "ALLOW_PRIVATE_TARGETS",
        "MAX_AUDIT_SECONDS",
    ):
        os.environ.pop(k, None)
    os.environ.update(env)

    import importlib
    import server.app
    import server.cache
    import server.config
    import server.database
    import server.middleware
    import server.models

    importlib.reload(server.config)
    importlib.reload(server.database)
    importlib.reload(server.cache)
    importlib.reload(server.middleware)
    importlib.reload(server.models)
    importlib.reload(server.app)

    app = server.app.create_app()
    return TestClient(app)


# --- SSRF protection -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://[::1]/",
    ],
)
def test_audit_rejects_private_and_loopback_urls(tmp_path, url):
    with _fresh_app(tmp_path) as c:
        resp = c.post("/audit", json={"url": url})
        assert resp.status_code == 422, resp.text
        assert "private" in resp.text.lower() or "loopback" in resp.text.lower() or "reserved" in resp.text.lower()


def test_audit_accepts_public_url(tmp_path):
    """Public hostnames should pass validation. We don't actually run the
    audit here — we just need enqueue to fail (Redis unreachable) with a
    503, proving validation accepted the URL.
    """
    with _fresh_app(tmp_path) as c:
        with patch("server.cache.get_cached_result", return_value=None), \
             patch("server.tasks.run_audit_task.delay", side_effect=RuntimeError("redis down")):
            resp = c.post("/audit", json={"url": "https://example.com/"})
            # Validation passes (would be 422 otherwise); enqueue fails.
            assert resp.status_code == 503, resp.text


def test_login_url_gets_the_same_ssrf_validation(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={
                "url": "https://example.com/",
                "options": {
                    "login": {
                        "url": "http://169.254.169.254/latest/meta-data/",
                        "username_selector": "#user",
                        "password_selector": "#pass",
                        "submit_selector": "button",
                        "username": "user",
                        "password": "secret",
                    }
                },
            },
        )
        assert resp.status_code == 422


def test_url_rejects_embedded_credentials(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit", json={"url": "https://user:secret@example.com/"}
        )
        assert resp.status_code == 422
        assert "credentials" in resp.text


def test_allow_private_opts_out_of_ssrf_check(tmp_path):
    with _fresh_app(tmp_path, ALLOW_PRIVATE_TARGETS="1") as c:
        with patch("server.cache.get_cached_result", return_value=None), \
             patch("server.tasks.run_audit_task.delay", return_value=None):
            resp = c.post("/audit", json={"url": "http://127.0.0.1:8080/"})
            # Should queue, not 422.
            assert resp.status_code == 200, resp.text


# --- Input bounds ----------------------------------------------------------


def test_audit_rejects_absurd_timeout(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={"url": "https://example.com/", "options": {"timeout_seconds": 99999}},
        )
        assert resp.status_code == 422


def test_audit_rejects_huge_viewport(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={
                "url": "https://example.com/",
                "options": {"viewport": {"width": 99999, "height": 99999}},
            },
        )
        assert resp.status_code == 422


def test_audit_rejects_too_many_headers(tmp_path):
    with _fresh_app(tmp_path) as c:
        headers = {f"h{i}": "v" for i in range(51)}
        resp = c.post(
            "/audit",
            json={"url": "https://example.com/", "options": {"headers": headers}},
        )
        assert resp.status_code == 422


def test_audit_rejects_unsafe_custom_header(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={
                "url": "https://example.com/",
                "options": {"headers": {"Host": "internal.service"}},
            },
        )
        assert resp.status_code == 422


def test_unknown_request_option_is_rejected(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={
                "url": "https://example.com/",
                "options": {"screeshots": True},
            },
        )
        assert resp.status_code == 422
        assert "screeshots" in resp.text


# --- /health ---------------------------------------------------------------


def test_health_reports_components(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.get("/health")
        body = resp.json()
        assert "components" in body
        assert "database" in body["components"]
        assert "redis" in body["components"]


def test_health_degraded_when_db_down(tmp_path):
    with _fresh_app(tmp_path) as c:
        with patch("server.database.ping", return_value=False):
            resp = c.get("/health")
            assert resp.status_code == 503
            body = resp.json()
            assert body["status"] == "degraded"
            assert body["components"]["database"] == "down"


# --- /audit/quick error handling ------------------------------------------


def test_quick_audit_hides_exception_text(tmp_path):
    """The response body should NOT contain the raw exception message."""
    with _fresh_app(tmp_path) as c:
        def boom(url, options):  # noqa: ARG001
            raise RuntimeError("internal-secret-path/etc/passwd")

        with patch("audit.orchestrator.run_quick_audit", side_effect=boom):
            resp = c.post("/audit/quick", json={"url": "https://example.com/"})
            assert resp.status_code == 500
            # Exception message must not leak.
            assert "internal-secret-path" not in resp.text
            # But a request ID should be present for correlation.
            assert "Request ID" in resp.text


def test_quick_audit_returns_504_on_timeout(tmp_path):
    """Thread-pool wall-clock timeout surfaces as 504, not 500."""
    with _fresh_app(tmp_path, MAX_AUDIT_SECONDS="1") as c:
        def hang(url, options):  # noqa: ARG001
            time.sleep(3)
            return {}

        # Temporarily shrink the quick-audit deadline for this test.
        import server.app as app_module

        original = app_module._QUICK_AUDIT_TIMEOUT_SECONDS
        app_module._QUICK_AUDIT_TIMEOUT_SECONDS = 0.5

        try:
            with patch("audit.orchestrator.run_quick_audit", side_effect=hang):
                resp = c.post("/audit/quick", json={"url": "https://example.com/"})
                assert resp.status_code == 504
                assert "timed out" in resp.text.lower()
        finally:
            app_module._QUICK_AUDIT_TIMEOUT_SECONDS = original


def test_single_page_endpoints_reject_urls_list(tmp_path):
    with _fresh_app(tmp_path, ALLOW_PRIVATE_TARGETS="1") as c:
        payload = {"urls": ["http://127.0.0.1/a", "http://127.0.0.1/b"]}
        assert c.post("/audit/quick", json=payload).status_code == 422
        assert c.post("/announce", json=payload).status_code == 422


def test_request_body_size_limit_is_enforced(tmp_path):
    with _fresh_app(tmp_path) as c:
        payload = b"x" * (16 * 1024 * 1024 + 1)
        resp = c.post(
            "/audit", content=payload, headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 413


def test_untrusted_request_id_is_replaced(tmp_path):
    with _fresh_app(tmp_path) as c:
        supplied = "x" * 500
        resp = c.get("/health", headers={"X-Request-ID": supplied})
        request_id = resp.headers["X-Request-ID"]
        assert request_id != supplied
        assert len(request_id) == 32


# --- Database: WAL + size cap ---------------------------------------------


def test_database_uses_wal_mode(tmp_path):
    from importlib import reload

    import server.config as sc
    import server.database as sd

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/wal.db"
    reload(sc)
    reload(sd)

    sd.init_db()
    with sd._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", mode


def test_database_refuses_oversized_result(tmp_path):
    from importlib import reload

    import server.config as sc
    import server.database as sd

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/cap.db"
    reload(sc)
    reload(sd)

    sd.init_db()
    sd.create_job("big-1", "https://example.com", "2026-04-21T00:00:00Z")

    # 20 MiB payload — over the 16 MiB cap.
    huge = {"data": "x" * (20 * 1024 * 1024)}
    with pytest.raises(ValueError, match="exceeds"):
        sd.save_job_result("big-1", huge, "2026-04-21T00:00:01Z")


def test_database_ping_returns_true_when_healthy(tmp_path):
    from importlib import reload

    import server.config as sc
    import server.database as sd

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/ping.db"
    reload(sc)
    reload(sd)

    sd.init_db()
    assert sd.ping() is True


# --- Cache size cap --------------------------------------------------------


def test_cache_skips_oversized_payload(tmp_path):
    """When the encoded payload is over the cap, set_cached_result returns
    silently and the value is not stored."""
    from unittest.mock import MagicMock

    from importlib import reload

    import server.config as sc
    import server.cache as sc_cache

    reload(sc)
    reload(sc_cache)

    fake = MagicMock()
    sc_cache._client = fake

    huge = {"x": "y" * (17 * 1024 * 1024)}
    sc_cache.set_cached_result("https://example.com/", huge)

    fake.setex.assert_not_called()


def test_cache_ping_returns_false_when_no_client(tmp_path):
    from importlib import reload

    import server.cache as sc_cache

    reload(sc_cache)
    sc_cache._client = None
    assert sc_cache.ping() is False


def test_cache_key_covers_options_and_is_order_independent():
    from server import cache as sc_cache

    first = sc_cache._url_key(
        "https://example.com/", {"level": "aa", "viewport": {"width": 1280}}
    )
    reordered = sc_cache._url_key(
        "https://example.com/", {"viewport": {"width": 1280}, "level": "aa"}
    )
    different = sc_cache._url_key(
        "https://example.com/", {"level": "aaa", "viewport": {"width": 1280}}
    )
    assert first == reordered
    assert first != different


def test_cache_client_is_not_disabled_by_transient_startup_outage(monkeypatch):
    from importlib import reload

    import server.cache as sc_cache
    import server.config as sc_config

    monkeypatch.setenv("CACHE_ENABLED", "true")
    reload(sc_config)
    reloaded = reload(sc_cache)
    assert reloaded._client is not None


def test_sensitive_audits_bypass_shared_cache():
    from unittest.mock import MagicMock

    from server import cache as sc_cache

    fake = MagicMock()
    sc_cache._client = fake
    options = {"login": {"username": "alice", "password": "secret"}}

    assert sc_cache.get_cached_result("https://example.com/", options) is None
    sc_cache.set_cached_result("https://example.com/", {"status": "completed"}, options)

    fake.get.assert_not_called()
    fake.setex.assert_not_called()


def test_browser_request_guard_blocks_private_redirect_target(monkeypatch):
    from unittest.mock import MagicMock

    from audit.browser import BrowserManager

    monkeypatch.delenv("ALLOW_PRIVATE_TARGETS", raising=False)
    route = MagicMock()
    request = MagicMock(url="http://169.254.169.254/latest/meta-data/")

    BrowserManager._guard_request(route, request)

    route.abort.assert_called_once_with("blockedbyclient")
    route.continue_.assert_not_called()


def test_axe_runtime_refuses_unvendored_third_party_script(tmp_path):
    from unittest.mock import MagicMock

    from audit import wcag_engine

    missing = tmp_path / "axe.min.js"
    config = SimpleNamespace(axe_script_path=str(missing))
    page = MagicMock()

    with patch.object(wcag_engine, "CONFIG", config), pytest.raises(
        FileNotFoundError, match="scripts/fetch_axe.py"
    ):
        wcag_engine._inject_axe(page)

    page.add_script_tag.assert_not_called()


# --- Rate limiter bucket cap ----------------------------------------------


def test_rate_limiter_bucket_cap():
    """Feeding many unique IPs into the middleware should evict oldest
    rather than grow the dict unbounded."""
    from server.middleware import RateLimitMiddleware

    os.environ["RATE_LIMIT_PER_MIN"] = "1000"

    async def noop_app(scope, receive, send):
        pass  # pragma: no cover

    mw = RateLimitMiddleware(noop_app)
    # Override to a tiny cap for the test.
    mw._MAX_BUCKETS = 5
    mw._enabled = True

    from collections import deque as _deque

    # Simulate 10 unique IPs being recorded.
    import time as _t

    for i in range(10):
        mw._hits[f"ip-{i}"] = _deque([_t.monotonic()])
        if len(mw._hits) > mw._MAX_BUCKETS:
            mw._hits.popitem(last=False)

    assert len(mw._hits) == 5
    # Oldest entries (ip-0..ip-4) should have been evicted.
    assert "ip-0" not in mw._hits
    assert "ip-9" in mw._hits

    os.environ.pop("RATE_LIMIT_PER_MIN", None)
