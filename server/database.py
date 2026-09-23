"""SQLite-backed storage for audit job results.

One table. Jobs are keyed by UUID. Results are stored as JSON blobs — we
don't need to query inside them.

Concurrency model: we enable WAL mode (one writer, many readers) and rely
on SQLite's built-in lock handling. The previous global threading.Lock
serialized all DB access across the process which deadlocked under
concurrent audits; with WAL + a generous busy_timeout we let SQLite
resolve contention itself, and fall back to raising if the timeout is
exceeded rather than blocking a Celery worker indefinitely.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any

from server.config import CONFIG

log = logging.getLogger(__name__)

# Cap JSON payload size when deserializing from the DB. 16 MiB is far
# bigger than any realistic audit result (typical results are < 500 KiB)
# but small enough that a malformed or malicious blob can't OOM the
# worker.
_MAX_RESULT_JSON_BYTES = 16 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audits (
    job_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    owner_key_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_url ON audits(url);
CREATE INDEX IF NOT EXISTS idx_audits_status ON audits(status);
CREATE INDEX IF NOT EXISTS idx_audits_owner ON audits(owner_key_id);
"""

# Columns added after the initial release. CREATE TABLE IF NOT EXISTS
# won't add them to a database created by an older version, so we
# reconcile on every init_db().
_MIGRATIONS = (
    ("owner_key_id", "ALTER TABLE audits ADD COLUMN owner_key_id TEXT"),
)


def _sqlite_path() -> str:
    # URL form: sqlite:///./data/audits.db
    url = CONFIG.database_url
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    return url


@contextmanager
def _connect():
    path = _sqlite_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # isolation_level=None → autocommit; each execute() is its own txn.
    # timeout=30 → SQLite waits up to 30s for a lock before SQLITE_BUSY.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Per-connection pragmas. WAL is a database-level setting but setting
    # it on each connection is cheap and idempotent.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.Error as exc:
        log.warning("failed to set SQLite pragmas: %s", exc)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(audits)")}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                log.info("migrating audits table: adding %s", column)
                conn.execute(ddl)


def _owner_clause(owner_key_id: str | None) -> tuple[str, tuple[Any, ...]]:
    """SQL fragment restricting a row lookup to one API key's jobs.

    Ownership is only enforced when the caller presents a key. With
    ``API_KEYS`` unset there is no principal to scope to, so every job is
    anonymous and the clause is empty.

    Rows written before this column existed have a NULL owner. They stay
    readable by any authenticated caller — refusing them would orphan
    every audit taken before the upgrade. New rows always carry an owner,
    so the gap closes as old jobs age out via cleanup_old_results().
    """
    if not owner_key_id:
        return "", ()
    return " AND (owner_key_id IS NULL OR owner_key_id = ?)", (owner_key_id,)


def create_job(
    job_id: str,
    url: str,
    created_at: str,
    owner_key_id: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audits "
            "(job_id, url, status, created_at, updated_at, owner_key_id) "
            "VALUES (?, ?, 'queued', ?, ?, ?)",
            (job_id, url, created_at, created_at, owner_key_id),
        )


def update_job_status(job_id: str, status: str, updated_at: str, error: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE audits SET status = ?, updated_at = ?, error = ? WHERE job_id = ?",
            (status, updated_at, error, job_id),
        )


def save_job_result(job_id: str, result: dict[str, Any], updated_at: str) -> None:
    payload = json.dumps(result)
    if len(payload.encode("utf-8")) > _MAX_RESULT_JSON_BYTES:
        raise ValueError(
            f"audit result exceeds {_MAX_RESULT_JSON_BYTES} bytes; refusing to persist"
        )
    with _connect() as conn:
        conn.execute(
            "UPDATE audits SET status = 'completed', result_json = ?, updated_at = ? "
            "WHERE job_id = ?",
            (payload, updated_at, job_id),
        )


def get_audit_result(
    job_id: str, owner_key_id: str | None = None
) -> dict[str, Any] | None:
    """Fetch one audit, optionally restricted to the key that created it.

    Job ids are UUIDs, but an unguessable identifier is not an access
    control: results carry target URLs, screenshots and the content of
    auth-gated pages. When the caller is authenticated we scope the
    lookup to their own jobs so one tenant cannot read another's.
    """
    clause, params = _owner_clause(owner_key_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT job_id, url, status, created_at, updated_at, result_json, error "
            "FROM audits WHERE job_id = ?" + clause,
            (job_id, *params),
        ).fetchone()

    if row is None:
        return None

    if row["result_json"]:
        raw = row["result_json"]
        if len(raw.encode("utf-8")) > _MAX_RESULT_JSON_BYTES:
            log.error("stored result_json for %s exceeds safety cap; refusing to load", job_id)
            return {
                "job_id": row["job_id"],
                "url": row["url"],
                "status": "failed",
                "timestamp": row["updated_at"],
                "duration_seconds": 0.0,
                "summary": {
                    "score": 0,
                    "grade": "?",
                    "total_issues": 0,
                    "by_severity": {"critical": 0, "serious": 0, "moderate": 0, "minor": 0},
                    "by_principle": {},
                },
                "issues": [],
                "modules": {},
                "error": "result payload exceeds size cap",
            }
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("corrupt result_json for %s: %s", job_id, exc)
            return None
        payload["job_id"] = row["job_id"]
        payload["status"] = row["status"]
        return payload

    return {
        "job_id": row["job_id"],
        "url": row["url"],
        "status": row["status"],
        "timestamp": row["updated_at"],
        "duration_seconds": 0.0,
        "summary": {
            "score": 0,
            "grade": "?",
            "total_issues": 0,
            "by_severity": {"critical": 0, "serious": 0, "moderate": 0, "minor": 0},
            "by_principle": {},
        },
        "issues": [],
        "modules": {},
        "error": row["error"],
    }


def delete_audit_result(job_id: str, owner_key_id: str | None = None) -> bool:
    """Delete one audit. Scoped to the owning key when authenticated."""
    clause, params = _owner_clause(owner_key_id)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM audits WHERE job_id = ?" + clause,
            (job_id, *params),
        )
        return cur.rowcount > 0


def ping() -> bool:
    """Quick health probe: can we open a connection and run a trivial query?"""
    try:
        with _connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error as exc:
        log.warning("database ping failed: %s", exc)
        return False


def cleanup_old_results(older_than_days: int, *, statuses: tuple[str, ...] = ("completed", "failed")) -> int:
    """Delete terminal-state audit rows older than the cutoff.

    Returns the number of rows deleted. Intended for a periodic cron /
    Celery beat task; the DB will otherwise grow forever.

    Rows in 'queued' or 'running' state are never deleted by this function,
    even if they're older than the cutoff — they might be legitimate
    long-running audits, and the caller probably wants to investigate
    manually if they've been stuck for days.
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be non-negative")

    import datetime as _dt

    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=older_than_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    placeholders = ",".join("?" for _ in statuses)
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM audits WHERE status IN ({placeholders}) AND updated_at < ?",  # nosec B608 - only placeholder count is interpolated
            (*statuses, cutoff),
        )
        return cur.rowcount
