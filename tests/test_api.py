"""API-level tests using FastAPI's TestClient.

These tests don't actually run Playwright — they exercise the request
validation, health endpoint, and the database / status flow. Full
Playwright-backed tests would go behind a --slow marker.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Point database at a throwaway path before importing the app.
    tmpdir = tmp_path_factory.mktemp("a11y")
    db_path = tmpdir / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    # Force NVDA off for deterministic behavior on any host.
    os.environ["SKIP_NVDA"] = "true"

    # Import lazily so env vars take effect.
    from server.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


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
