"""Start a Celery worker.

    # Linux worker (default queue — everything except NVDA):
    python scripts/run_worker.py

    # Windows worker (NVDA queue only):
    set CELERY_QUEUES=nvda
    set CELERY_POOL=solo
    python scripts/run_worker.py

Environment:
- CELERY_QUEUES       Comma-separated queues to consume. Default "default".
                      Set to "nvda" on the Windows worker; set to
                      "default,nvda" to run both on one host.
- CELERY_POOL         Pool implementation. Defaults to "prefork" on Linux
                      (process-based parallelism) and "solo" on Windows
                      (prefork is unavailable).
- CELERY_CONCURRENCY  Worker processes/threads. Default 2 on Linux; always
                      1 in solo mode on Windows.
- CELERY_LOGLEVEL     Default INFO.
"""

from __future__ import annotations

import os
import platform
import sys

from celery_app import celery_app


def _default_pool() -> str:
    # Playwright's sync API is greenlet-based and not safe across OS
    # threads, so eventlet/gevent pools are out. On Windows prefork is
    # unavailable; "solo" is the only viable option.
    if platform.system() == "Windows":
        return "solo"
    return "prefork"


def main() -> int:
    queues = os.environ.get("CELERY_QUEUES", "default")
    pool = os.environ.get("CELERY_POOL", _default_pool())
    concurrency = os.environ.get("CELERY_CONCURRENCY", "2")
    loglevel = os.environ.get("CELERY_LOGLEVEL", "INFO")

    # In solo mode Celery ignores --concurrency; don't pass it.
    extra: list[str] = []
    if pool != "solo":
        extra.append(f"--concurrency={concurrency}")

    argv = [
        "worker",
        f"--loglevel={loglevel}",
        f"--pool={pool}",
        f"--queues={queues}",
        # hostname helps operators spot which worker is which in logs /
        # flower / redis keys. Defaults to `celery@<machine>` — we
        # specialize by queue so a Windows NVDA worker is identifiable.
        f"--hostname=celery@%h-{queues.replace(',', '-')}",
        *extra,
        *sys.argv[1:],
    ]
    celery_app.worker_main(argv=argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
