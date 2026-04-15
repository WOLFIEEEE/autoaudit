"""Tests for database.cleanup_old_results."""

from __future__ import annotations

import datetime as dt
import importlib
import os

import pytest


@pytest.fixture
def fresh_db(tmp_path):
    """Rebuild the database module against a throwaway SQLite file."""
    db_path = tmp_path / "cleanup_test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    import server.config
    import server.database

    importlib.reload(server.config)
    importlib.reload(server.database)
    server.database.init_db()
    return server.database


def _iso(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_cleanup_respects_cutoff(fresh_db):
    now = dt.datetime.now(dt.timezone.utc)
    old = now - dt.timedelta(days=40)
    recent = now - dt.timedelta(days=5)

    fresh_db.create_job("old-a", "https://a.example", _iso(old))
    fresh_db.save_job_result("old-a", {}, _iso(old))

    fresh_db.create_job("recent-b", "https://b.example", _iso(recent))
    fresh_db.save_job_result("recent-b", {}, _iso(recent))

    deleted = fresh_db.cleanup_old_results(older_than_days=30)
    assert deleted == 1
    # old-a is gone, recent-b stays.
    assert fresh_db.get_audit_result("old-a") is None
    assert fresh_db.get_audit_result("recent-b") is not None


def test_cleanup_skips_queued_and_running(fresh_db):
    now = dt.datetime.now(dt.timezone.utc)
    ancient = _iso(now - dt.timedelta(days=365))

    # Queued + running rows that are ancient — must NOT be deleted.
    fresh_db.create_job("queued-old", "https://q.example", ancient)
    fresh_db.create_job("running-old", "https://r.example", ancient)
    fresh_db.update_job_status("running-old", "running", ancient)

    # A completed row that should be deleted.
    fresh_db.create_job("done-old", "https://d.example", ancient)
    fresh_db.save_job_result("done-old", {}, ancient)

    deleted = fresh_db.cleanup_old_results(older_than_days=30)
    assert deleted == 1
    assert fresh_db.get_audit_result("queued-old") is not None
    assert fresh_db.get_audit_result("running-old") is not None
    assert fresh_db.get_audit_result("done-old") is None


def test_cleanup_zero_days_is_strict_now(fresh_db):
    # older_than_days=0 should delete anything with updated_at <= now.
    now = dt.datetime.now(dt.timezone.utc)
    just_now = _iso(now - dt.timedelta(seconds=1))

    fresh_db.create_job("x", "https://x.example", just_now)
    fresh_db.save_job_result("x", {}, just_now)

    deleted = fresh_db.cleanup_old_results(older_than_days=0)
    assert deleted == 1


def test_negative_days_raises(fresh_db):
    with pytest.raises(ValueError):
        fresh_db.cleanup_old_results(older_than_days=-1)


def test_empty_db_is_noop(fresh_db):
    assert fresh_db.cleanup_old_results(older_than_days=30) == 0
