"""Delete old audit rows from the SQLite store.

Intended to run periodically (cron, systemd timer, Celery beat, k8s
CronJob). The DB is append-only otherwise and will grow forever.

    python scripts/cleanup_audits.py --days 30

Only completed / failed audits are eligible for deletion. Queued or
running rows are never touched — if they're stale, the ops team should
investigate rather than have this script silently drop them.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running as `python scripts/cleanup_audits.py` from the project root.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.database import cleanup_old_results, init_db  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Delete completed/failed audits older than this many days (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the count that would be deleted and exit without deleting",
    )
    args = parser.parse_args(argv)

    init_db()

    if args.dry_run:
        # Run with an impossible-to-match status so we don't delete,
        # then report the count we WOULD delete.
        import datetime as _dt

        import sqlite3

        from server.config import CONFIG

        path = CONFIG.database_url.replace("sqlite:///", "", 1)
        cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=args.days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        conn = sqlite3.connect(path)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM audits WHERE status IN ('completed','failed') AND updated_at < ?",
                (cutoff,),
            ).fetchone()[0]
        finally:
            conn.close()
        print(f"Would delete {n} audit(s) older than {args.days} days")
        return 0

    n = cleanup_old_results(args.days)
    print(f"Deleted {n} audit(s) older than {args.days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
