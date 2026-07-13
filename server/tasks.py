"""Celery tasks.

Two tasks, routed to separate queues:

- `audit.run`       (queue=default) — Linux-friendly. Runs Path A +
  every automated module. When the audit options request real NVDA
  (Path B) and we're not on Windows, this task enqueues
  `audit.run_nvda` to let a Windows worker append the NVDA findings.

- `audit.run_nvda`  (queue=nvda)     — Windows-only. Loads the partial
  audit, runs Path B against the same URL, and merges the NVDA
  issues + transcript back into the stored result.

Both tasks write to the same SQLite row keyed by `job_id`. The result
payload is a single JSON blob; Path B appends to it non-destructively.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded

from celery_app import celery_app
from server import cache, database

log = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@celery_app.task(
    name="audit.run",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def run_audit_task(
    self,
    job_id: str,
    url: str,
    options: dict[str, Any],
    urls: list[str] | None = None,
) -> dict[str, Any]:
    """Run a full audit (Path A + all automated modules).

    Single-URL when `urls` is None; multi-URL when `urls` is a list
    (the API sets it for requests that pass `urls` instead of `url`).
    """
    from audit.orchestrator import AuditOrchestrator

    start = time.time()
    database.update_job_status(job_id, "running", _now())

    try:
        if urls:
            orchestrator = AuditOrchestrator(urls=urls, options=options)
        else:
            orchestrator = AuditOrchestrator(url=url, options=options)
        result = orchestrator.run()
        result["job_id"] = job_id
        result["status"] = "completed"
        database.save_job_result(job_id, result, _now())

        # Decide whether Path B is needed. If so, enqueue a Windows-only
        # task. We do NOT cache the partial result — only cache the
        # final, post-NVDA version.
        if result.get("nvda_status") == "pending":
            try:
                run_nvda_task.apply_async(
                    kwargs={
                        "job_id": job_id,
                        "url": url,
                        "options": options,
                        "cache_result": not bool(urls),
                    },
                    # Expire the message after an hour. If no Windows
                    # worker is online, the NVDA pass is effectively
                    # skipped rather than queued indefinitely.
                    expires=3600,
                )
                log.info("enqueued NVDA follow-up for %s", job_id)
            except Exception as exc:
                log.warning("failed to enqueue NVDA follow-up for %s: %s", job_id, exc)
                # Not fatal — Path A result is already persisted.
                result["nvda_status"] = "enqueue_failed"
                database.save_job_result(job_id, result, _now())
        elif not urls:
            # No NVDA pending — the result is final.
            cache.set_cached_result(url, result, options)

        return result

    except SoftTimeLimitExceeded:
        elapsed = round(time.time() - start, 1)
        log.warning("audit %s exceeded soft time limit after %ss", job_id, elapsed)
        database.update_job_status(
            job_id,
            "failed",
            _now(),
            error=f"audit exceeded time limit ({elapsed}s)",
        )
        return {"job_id": job_id, "status": "failed", "error": "timeout"}
    except Exception as exc:
        log.exception("audit %s failed", job_id)
        database.update_job_status(
            job_id,
            "failed",
            _now(),
            error=f"audit failed ({type(exc).__name__})",
        )
        raise


@celery_app.task(
    name="audit.run_nvda",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def run_nvda_task(
    self,
    job_id: str,
    url: str,
    options: dict[str, Any],
    cache_result: bool = True,
) -> dict[str, Any]:
    """Path B NVDA follow-up. Runs ONLY on a Windows worker (queue=nvda).

    Merges NVDA findings into the already-saved audit result and
    refreshes the top-level summary + cache.
    """
    from audit.deduplicator import deduplicate_issues
    from audit.orchestrator import run_nvda_follow_up
    from audit.scorer import calculate_scores

    log.info("NVDA follow-up starting for %s", job_id)

    existing = database.get_audit_result(job_id)
    if not existing:
        log.warning("NVDA task: no existing audit row for %s; aborting", job_id)
        return {"job_id": job_id, "status": "skipped", "reason": "no parent audit"}

    try:
        patch = run_nvda_follow_up(url, options)
    except SoftTimeLimitExceeded:
        database.update_job_status(
            job_id,
            existing.get("status", "completed"),
            _now(),
            error="NVDA follow-up exceeded time limit",
        )
        return {"job_id": job_id, "nvda_status": "failed"}
    except Exception as exc:
        log.exception("NVDA task %s failed", job_id)
        existing["nvda_status"] = "failed"
        existing.setdefault("modules", {}).setdefault("screen_reader", {})
        existing["modules"]["screen_reader"]["nvda_error"] = (
            f"NVDA follow-up failed ({type(exc).__name__})"
        )
        database.save_job_result(job_id, existing, _now())
        raise

    # Merge patch into the stored result.
    existing["nvda_status"] = patch.get("nvda_status", "completed")

    # Attach raw NVDA result under screen_reader module.
    modules = existing.setdefault("modules", {})
    sr_summary = modules.setdefault(
        "screen_reader",
        {"ran": False, "issues_found": 0, "duration_seconds": 0.0, "error": None},
    )
    sr_summary["nvda"] = patch.get("nvda", {})

    # Append new issues, re-dedupe, recompute summary.
    new_issues = patch.get("issues") or []
    if new_issues:
        all_issues = list(existing.get("issues") or []) + new_issues
        all_issues = deduplicate_issues(all_issues)
        # Re-sort by severity — same rank map the orchestrator uses.
        rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
        all_issues.sort(key=lambda i: rank.get(i.get("severity", "minor"), 4))
        existing["issues"] = all_issues
        existing["summary"] = calculate_scores(all_issues)
        sr_summary["issues_found"] = sum(
            1 for i in all_issues if i.get("module") == "screen_reader"
        )

    database.save_job_result(job_id, existing, _now())
    # Now that NVDA has landed, it's safe to cache.
    if cache_result:
        cache.set_cached_result(url, existing, options)

    log.info("NVDA follow-up for %s: %s", job_id, existing["nvda_status"])
    return {"job_id": job_id, "nvda_status": existing["nvda_status"]}
