"""FastAPI application factory and route definitions."""

from __future__ import annotations

import logging
import platform
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from server import cache, database
from server.config import CONFIG
from server.middleware import (
    ApiKeyMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    configure_logging,
)
from server.models import AuditRequest, AuditStatus

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging()
    database.init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Accessibility Audit Server",
        description=(
            "Full WCAG 2.2 accessibility audit server. "
            "See README for robots.txt policy and scope."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )

    # Outer-first application order — RequestId wraps everything so even
    # auth failures get a request ID. Rate limiting comes after auth so
    # it can key off the API-key ID we stash in request.state.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ApiKeyMiddleware)
    app.add_middleware(RequestIdMiddleware)

    jinja = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        # Unconditional autoescape: templates here are HTML reports and we
        # render untrusted content (html_snippets, URLs) into them.
        # select_autoescape matches by final extension — our files end in
        # .j2 which wouldn't match a naive ["html"] list.
        autoescape=True,
    )

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "platform": platform.system(),
            "nvda_capable": platform.system() == "Windows",
            "skip_nvda_default": CONFIG.default_skip_nvda,
        }

    @app.post("/audit", response_model=AuditStatus, tags=["audit"])
    def start_audit(request: AuditRequest) -> AuditStatus:
        cached = cache.get_cached_result(request.url)
        if cached:
            return AuditStatus(
                job_id=cached["job_id"],
                status="completed",
                poll_url=f"/audit/{cached['job_id']}",
            )

        job_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        database.create_job(job_id, request.url, now)

        try:
            from server.tasks import run_audit_task

            run_audit_task.delay(
                job_id=job_id,
                url=request.url,
                options=request.options.model_dump(),
            )
        except Exception as exc:
            log.warning("failed to enqueue audit %s: %s", job_id, exc)
            database.update_job_status(job_id, "failed", now, error=f"enqueue failed: {exc}")
            raise HTTPException(503, "Task queue unavailable. Is Redis/Celery running?")

        log.info("queued audit %s for %s", job_id, request.url)
        return AuditStatus(
            job_id=job_id,
            status="queued",
            estimated_seconds=60,
            poll_url=f"/audit/{job_id}",
        )

    @app.get("/audit/{job_id}", tags=["audit"])
    def get_audit(job_id: str) -> dict:
        result = database.get_audit_result(job_id)
        if not result:
            raise HTTPException(404, "Audit not found")
        return result

    @app.get("/audit/{job_id}/html", response_class=HTMLResponse, tags=["audit"])
    def get_audit_html(job_id: str) -> HTMLResponse:
        """Human-readable HTML report for a completed audit."""
        result = database.get_audit_result(job_id)
        if not result:
            raise HTTPException(404, "Audit not found")
        template = jinja.get_template("report.html.j2")
        return HTMLResponse(template.render(audit=result))

    @app.delete("/audit/{job_id}", tags=["audit"])
    def delete_audit(job_id: str) -> dict:
        if not database.delete_audit_result(job_id):
            raise HTTPException(404, "Audit not found")
        return {"deleted": job_id}

    @app.post("/audit/quick", tags=["audit"])
    def quick_audit(request: AuditRequest) -> dict:
        """Synchronous lightweight scan — axe-core only. No queue required."""
        from audit.orchestrator import run_quick_audit

        try:
            return run_quick_audit(request.url, request.options.model_dump())
        except Exception as exc:
            log.exception("quick audit failed")
            raise HTTPException(500, f"Quick audit failed: {exc}")

    return app
