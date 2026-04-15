"""Celery application factory.

Uses Redis as both broker and result backend. Tasks are defined in
server/tasks.py and auto-discovered.
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
)
