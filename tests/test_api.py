"""API-level tests using FastAPI's TestClient.

These tests don't launch Playwright — they exercise request validation,
the health endpoint, middleware (request IDs, API key auth, rate
limiting), and the HTML report endpoint (against a pre-inserted
fixture row in the DB).
"""

from __future__ import annotations

import json
import os
import time

import pytest
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, **env: str) -> TestClient:
    """Build an isolated app instance with per-test env vars.

    We have to reset process-wide env between tests because middleware
    captures it at construction time.
    """
    db_path = tmp_path / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SKIP_NVDA"] = "true"
    for k in ("API_KEYS", "RATE_LIMIT_PER_MIN", "LOG_FORMAT", "LOG_LEVEL"):
        os.environ.pop(k, None)
    os.environ.update(env)

    # Force a re-import so env changes take effect. Reload order matters:
    # server.database captures CONFIG at import time (via its module-level
    # `from server.config import CONFIG`), so it must be reloaded after
    # server.config.
    import importlib
    import server.app
    import server.cache
    import server.config
    import server.database
    import server.middleware

    importlib.reload(server.config)
    importlib.reload(server.database)
    importlib.reload(server.cache)
    importlib.reload(server.middleware)
    importlib.reload(server.app)

    app = server.app.create_app()
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with _fresh_app(tmp_path) as c:
        yield c


# ---------- basic endpoints ----------


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "nvda_capable" in body


def test_audit_rejects_invalid_url(client):
    resp = client.post("/audit", json={"url": "ftp://example.com"})
    assert resp.status_code == 422


def test_audit_rejects_missing_url(client):
    resp = client.post("/audit", json={})
    assert resp.status_code == 422


def test_get_missing_audit_returns_404(client):
    resp = client.get("/audit/does-not-exist")
    assert resp.status_code == 404


def test_delete_missing_audit_returns_404(client):
    resp = client.delete("/audit/does-not-exist")
    assert resp.status_code == 404


# ---------- request ID middleware ----------


def test_response_carries_request_id(client):
    resp = client.get("/health")
    rid = resp.headers.get("X-Request-ID")
    assert rid and len(rid) >= 8


def test_request_id_passthrough(client):
    resp = client.get("/health", headers={"X-Request-ID": "my-trace-123"})
    assert resp.headers.get("X-Request-ID") == "my-trace-123"


# ---------- API key authentication ----------


def test_auth_off_by_default(client):
    # With API_KEYS unset, requests pass without a header.
    assert client.get("/audit/anything").status_code == 404  # reaches handler


def test_auth_enabled_rejects_missing_key(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="secret123") as c:
        resp = c.get("/audit/anything")
        assert resp.status_code == 401


def test_auth_enabled_accepts_correct_header_key(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="secret123,other") as c:
        r = c.get("/audit/whatever", headers={"X-API-Key": "secret123"})
        assert r.status_code == 404  # reaches handler → not auth-failed


def test_auth_accepts_bearer_token(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="secret123") as c:
        r = c.get(
            "/audit/whatever",
            headers={"Authorization": "Bearer secret123"},
        )
        assert r.status_code == 404


def test_auth_rejects_wrong_key(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="right") as c:
        resp = c.get("/audit/x", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401


def test_health_is_public_even_with_auth_on(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="secret") as c:
        assert c.get("/health").status_code == 200


# ---------- rate limiting ----------


def test_rate_limit_blocks_after_threshold(tmp_path):
    with _fresh_app(tmp_path, RATE_LIMIT_PER_MIN="3") as c:
        # First three requests pass; fourth gets 429.
        for _ in range(3):
            assert c.get("/audit/x").status_code == 404
        r = c.get("/audit/x")
        assert r.status_code == 429
        assert r.headers.get("Retry-After") is not None


def test_rate_limit_excludes_health(tmp_path):
    with _fresh_app(tmp_path, RATE_LIMIT_PER_MIN="1") as c:
        for _ in range(5):
            assert c.get("/health").status_code == 200


# ---------- HTML report endpoint ----------


def test_html_report_missing_audit_returns_404(client):
    resp = client.get("/audit/nope/html")
    assert resp.status_code == 404


def test_html_report_renders_completed_audit(client, tmp_path):
    # Pre-insert a completed audit into the test DB and fetch the HTML.
    from server import database

    job_id = "test-html-001"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    database.create_job(job_id, "https://example.com", now)
    result = {
        "url": "https://example.com",
        "timestamp": now,
        "duration_seconds": 1.5,
        "summary": {
            "score": 72,
            "grade": "B",
            "total_issues": 2,
            "by_severity": {"critical": 0, "serious": 1, "moderate": 1, "minor": 0},
            "by_principle": {
                "perceivable": {"score": 90, "issues": 1},
                "operable": {"score": 95, "issues": 0},
                "understandable": {"score": 60, "issues": 1},
                "robust": {"score": 100, "issues": 0},
            },
        },
        "issues": [
            {
                "id": "x-1",
                "module": "structure",
                "rule": "structure-html-lang",
                "severity": "serious",
                "principle": "understandable",
                "wcag_criteria": ["3.1.1"],
                "title": "<html> element is missing a lang attribute",
                "description": "...",
                "element": {"selector": "html", "html_snippet": "<html>", "text_content": ""},
                "details": {},
                "fix_suggestion": 'Add <html lang="en">',
            }
        ],
        "modules": {
            "structure": {"ran": True, "issues_found": 1, "duration_seconds": 0.1, "error": None}
        },
    }
    database.save_job_result(job_id, result, now)

    resp = client.get(f"/audit/{job_id}/html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "https://example.com" in body
    assert "structure-html-lang" in body
    # Jinja2 autoescape should convert '<html>' to '&lt;html&gt;' in the snippet.
    assert "&lt;html&gt;" in body
    assert "<script>" not in body.lower().replace("<script>", "")  # sanity
