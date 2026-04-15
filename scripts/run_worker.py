"""Helper to start a Celery worker.

    python scripts/run_worker.py
"""

from __future__ import annotations

import sys

from celery_app import celery_app


def main() -> int:
    argv = [
        "worker",
        "--loglevel=INFO",
        # --pool=solo is required on Windows; safe fallback elsewhere.
        "--pool=solo",
        *sys.argv[1:],
    ]
    celery_app.worker_main(argv=argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
