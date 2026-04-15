"""Celery tasks for running long audits in the background."""

from __future__ import annotations

import logging
import time
from typing import Any

from celery_app import celery_app
from server import cache, database

log = logging.getLogger(__name__)


@celery_app.task(name="audit.run", bind=True)
def run_audit_task(self, job_id: str, url: str, options: dict[str, Any]) -> dict[str, Any]:
    """Run a full audit. Writes result to the database when done."""
    from audit.orchestrator import AuditOrchestrator

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    database.update_job_status(job_id, "running", now)

    try:
        orchestrator = AuditOrchestrator(url=url, options=options)
        result = orchestrator.run()
        result["job_id"] = job_id
        result["status"] = "completed"
        database.save_job_result(job_id, result, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        cache.set_cached_result(url, result)
        return result
    except Exception as exc:
        log.exception("audit %s failed", job_id)
        database.update_job_status(
            job_id,
            "failed",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            error=str(exc),
        )
        raise
