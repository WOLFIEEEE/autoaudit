"""Authentication posture and per-key job scoping.

Two properties, both previously missing:

1. **Fail closed.** With no API_KEYS the server used to serve everything.
   It now refuses unless ALLOW_ANONYMOUS=1 makes that an explicit choice.
2. **Job ownership.** Job ids are UUIDs, but an unguessable id is not an
   access control — audit results carry target URLs, screenshots and the
   content of auth-gated pages. An authenticated caller must not be able
   to read another key's audit.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, **env: str) -> TestClient:
    db_path = tmp_path / "auth.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SKIP_NVDA"] = "true"
    os.environ["CACHE_ENABLED"] = "false"
    for k in ("API_KEYS", "ALLOW_ANONYMOUS", "RATE_LIMIT_PER_MIN"):
        os.environ.pop(k, None)
    os.environ.update(env)

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
    return TestClient(server.app.create_app())


@pytest.fixture(autouse=True)
def _restore_suite_default():
    """conftest sets ALLOW_ANONYMOUS=1 for the rest of the suite; these
    tests clear it, so put it back afterwards."""
    yield
    os.environ["ALLOW_ANONYMOUS"] = "1"


# ---------- fail closed ----------


def test_no_keys_and_no_opt_in_refuses_audit_endpoints(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.get("/audit/some-job-id")
        assert resp.status_code == 503
        assert "API_KEYS" in resp.json()["detail"]


def test_health_stays_public_when_failing_closed(tmp_path):
    """Probes must still work or the container never passes readiness."""
    with _fresh_app(tmp_path) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/openapi.json").status_code == 200


def test_allow_anonymous_opts_into_an_open_server(tmp_path):
    with _fresh_app(tmp_path, ALLOW_ANONYMOUS="1") as c:
        # 404, not 503 — the request was served, the job just isn't there.
        assert c.get("/audit/missing").status_code == 404


def test_keys_configured_still_requires_one(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="alpha,beta") as c:
        assert c.get("/audit/missing").status_code == 401
        assert (
            c.get("/audit/missing", headers={"X-API-Key": "alpha"}).status_code == 404
        )


# ---------- job ownership ----------


def _seed_job(job_id: str, owner: str | None) -> None:
    import server.database as database

    database.create_job(
        job_id,
        "https://example.com",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        owner_key_id=owner,
    )


def _key_id(raw: str) -> str:
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_one_key_cannot_read_another_keys_audit(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="alpha,beta") as c:
        _seed_job("job-owned-by-alpha", _key_id("alpha"))

        mine = c.get("/audit/job-owned-by-alpha", headers={"X-API-Key": "alpha"})
        assert mine.status_code == 200

        theirs = c.get("/audit/job-owned-by-alpha", headers={"X-API-Key": "beta"})
        assert theirs.status_code == 404, "beta must not read alpha's audit"


def test_one_key_cannot_delete_another_keys_audit(tmp_path):
    with _fresh_app(tmp_path, API_KEYS="alpha,beta") as c:
        _seed_job("job-owned-by-alpha", _key_id("alpha"))

        assert (
            c.delete("/audit/job-owned-by-alpha", headers={"X-API-Key": "beta"})
        ).status_code == 404
        # Still there for the real owner.
        assert (
            c.get("/audit/job-owned-by-alpha", headers={"X-API-Key": "alpha"})
        ).status_code == 200


def test_derived_report_endpoints_are_scoped_too(tmp_path):
    """HTML / VPAT / XLSX render the same row — scoping the JSON endpoint
    alone would leave the data readable through a side door."""
    with _fresh_app(tmp_path, API_KEYS="alpha,beta") as c:
        _seed_job("job-owned-by-alpha", _key_id("alpha"))
        for path in ("html", "vpat", "vpat.html", "xlsx"):
            resp = c.get(
                f"/audit/job-owned-by-alpha/{path}",
                headers={"X-API-Key": "beta"},
            )
            assert resp.status_code == 404, f"/{path} leaked to the wrong key"


def test_legacy_rows_without_an_owner_stay_readable(tmp_path):
    """Pre-migration jobs have a NULL owner. Refusing them would orphan
    every audit taken before the upgrade."""
    with _fresh_app(tmp_path, API_KEYS="alpha") as c:
        _seed_job("legacy-job", None)
        assert (
            c.get("/audit/legacy-job", headers={"X-API-Key": "alpha"})
        ).status_code == 200


def test_new_jobs_record_their_owner(tmp_path):
    import server.database as database

    with _fresh_app(tmp_path, API_KEYS="alpha") as c:
        resp = c.post(
            "/audit",
            json={"url": "https://example.com"},
            headers={"X-API-Key": "alpha"},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

    with database._connect() as conn:
        row = conn.execute(
            "SELECT owner_key_id FROM audits WHERE job_id = ?", (job_id,)
        ).fetchone()
    assert row["owner_key_id"] == _key_id("alpha")
