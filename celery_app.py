"""Celery application factory.

Uses Redis as both broker and result backend. Tasks are defined in
server/tasks.py and auto-discovered.

Two queues:
- `default` — Linux workers. Runs Path A (Chromium a11y-tree) + all
  automated modules. Platform-agnostic.
- `nvda`    — Windows worker(s). Runs Path B (real NVDA speech capture)
  as a follow-up task. See docs/windows_worker.md for the laptop/VM
  setup.

Only `audit.run_nvda` is routed to `nvda`; everything else stays on
`default`. A worker chooses its queues with `--queues=...` or
`CELERY_QUEUES=...` (see scripts/run_worker.py).
"""

from __future__ import annotations

from celery import Celery

from server.config import CONFIG

celery_app = Celery(
    "a11y_audit",
    broker=CONFIG.redis_url,
    backend=CONFIG.redis_url,
    include=["server.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_time_limit=CONFIG.max_audit_seconds + 30,
    task_soft_time_limit=CONFIG.max_audit_seconds,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_default_queue="default",
    task_routes={
        "audit.run_nvda": {"queue": "nvda"},
    },
    # TTL for NVDA jobs: if no Windows worker is online within this
    # window, the message expires rather than piling up in Redis.
    # One hour is generous enough for a laptop to come back from sleep.
    task_default_rate_limit=None,
    broker_transport_options={
        "visibility_timeout": 3600,
    },
)
