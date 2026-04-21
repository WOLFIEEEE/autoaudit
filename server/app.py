"""FastAPI application factory and route definitions."""

from __future__ import annotations

import asyncio
import logging
import platform
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from starlette.concurrency import run_in_threadpool

from server import cache, database
from server.config import CONFIG
from server.middleware import (
    ApiKeyMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    configure_logging,
    current_request_id,
)
from server.models import AuditRequest, AuditStatus

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Upper bound for the synchronous /audit/quick endpoint. Must always be
# <= max_audit_seconds so we can surface a clean 504 before the client
# times out. This is independent of the request-body timeout_seconds
# (which bounds Playwright navigation, not the whole audit).
_QUICK_AUDIT_TIMEOUT_SECONDS = min(60, CONFIG.max_audit_seconds)


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
        """Liveness + dependency probe.

        Returns 200 with status="ok" when the DB + Redis (if configured)
        are reachable. Returns 503 with status="degraded" when a
        dependency check fails so load balancers and orchestrators can
        gate traffic.
        """
        db_ok = database.ping()
        redis_ok = cache.ping()
        redis_configured = cache._client is not None  # noqa: SLF001
        # We treat Redis as required only when it was successfully
        # configured at import time. If Redis was never reachable
        # (single-machine dev), we shouldn't claim the server is
        # degraded — caching is optional.
        components = {
            "database": "ok" if db_ok else "down",
            "redis": ("ok" if redis_ok else "down") if redis_configured else "not_configured",
        }
        healthy = db_ok and (redis_ok or not redis_configured)
        body = {
            "status": "ok" if healthy else "degraded",
            "components": components,
            "platform": platform.system(),
            "nvda_capable": platform.system() == "Windows",
            "skip_nvda_default": CONFIG.default_skip_nvda,
        }
        if not healthy:
            # Starlette returns 200 by default; use an HTTPException-style
            # response with 503 so orchestrators can route on status code.
            from fastapi.responses import JSONResponse

            return JSONResponse(body, status_code=503)
        return body

    @app.post("/audit", response_model=AuditStatus, tags=["audit"])
    def start_audit(request: AuditRequest) -> AuditStatus:
        urls = request.target_urls()
        # Cache only single-URL requests. Multi-URL aggregate results
        # have too many option permutations to key reliably.
        if len(urls) == 1:
            cached = cache.get_cached_result(urls[0])
            if cached:
                return AuditStatus(
                    job_id=cached["job_id"],
                    status="completed",
                    poll_url=f"/audit/{cached['job_id']}",
                )

        job_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        database.create_job(job_id, urls[0], now)

        try:
            from server.tasks import run_audit_task

            run_audit_task.delay(
                job_id=job_id,
                url=urls[0],
                options=request.options.model_dump(),
                urls=urls if len(urls) > 1 else None,
            )
        except Exception as exc:
            log.warning("failed to enqueue audit %s: %s", job_id, exc)
            database.update_job_status(job_id, "failed", now, error=f"enqueue failed: {exc}")
            # Don't leak broker internals in the response body.
            raise HTTPException(503, "Task queue unavailable")

        log.info("queued audit %s for %d url(s)", job_id, len(urls))
        return AuditStatus(
            job_id=job_id,
            status="queued",
            estimated_seconds=60 * len(urls),
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
    async def quick_audit(request: AuditRequest) -> dict:
        """Synchronous lightweight scan — axe-core only. No queue required.

        Runs in a thread pool so Playwright's sync API doesn't block the
        event loop (which would otherwise stall `/health` and every
        other request on this worker). Wrapped in a wall-clock timeout
        independent of Playwright's per-action timeout — if the audit
        overshoots we return 504 and let the browser process cleanup
        itself.
        """
        from audit.orchestrator import run_quick_audit

        url = request.url
        options = request.options.model_dump()

        try:
            return await asyncio.wait_for(
                run_in_threadpool(run_quick_audit, url, options),
                timeout=_QUICK_AUDIT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            rid = current_request_id() or "-"
            log.warning("quick audit timed out after %ss (request_id=%s)",
                        _QUICK_AUDIT_TIMEOUT_SECONDS, rid)
            raise HTTPException(
                504,
                f"Quick audit timed out after {_QUICK_AUDIT_TIMEOUT_SECONDS}s. "
                f"Request ID: {rid}",
            )
        except Exception:
            rid = current_request_id() or "-"
            # Log full traceback locally; don't leak details to the client.
            log.exception("quick audit failed (request_id=%s)", rid)
            raise HTTPException(500, f"Quick audit failed. Request ID: {rid}")

    return app
