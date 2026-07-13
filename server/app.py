"""FastAPI application factory and route definitions."""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from starlette.concurrency import run_in_threadpool

from server import cache, database
from server.config import CONFIG
from server.middleware import (
    ApiKeyMiddleware,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
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
    app.add_middleware(RequestBodyLimitMiddleware)
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
        components = {
            "database": "ok" if db_ok else "down",
            "redis": (
                "disabled"
                if not CONFIG.cache_enabled
                else ("ok" if redis_ok else "down")
            ),
        }
        healthy = db_ok and (redis_ok or not CONFIG.redis_required)
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
        options = request.options.model_dump(mode="json")
        # Cache only single-URL requests. Multi-URL aggregate results
        # have too many option permutations to key reliably.
        if len(urls) == 1:
            cached = cache.get_cached_result(urls[0], options)
            if cached:
                cached_job_id = cached.get("job_id")
                if cached_job_id and database.get_audit_result(cached_job_id):
                    return AuditStatus(
                        job_id=cached_job_id,
                        status="completed",
                        poll_url=f"/audit/{cached_job_id}",
                    )
                cache.delete_cached_result(urls[0], options)

        job_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        database.create_job(job_id, urls[0], now)

        try:
            from server.tasks import run_audit_task

            run_audit_task.delay(
                job_id=job_id,
                url=urls[0],
                options=options,
                urls=urls if len(urls) > 1 else None,
            )
        except Exception as exc:
            log.warning("failed to enqueue audit %s: %s", job_id, exc)
            database.update_job_status(
                job_id,
                "failed",
                now,
                error=f"task enqueue failed ({type(exc).__name__})",
            )
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

    @app.get("/audit/{job_id}/vpat", tags=["audit"])
    def get_audit_vpat(job_id: str, target_level: str = "AA") -> dict:
        """VPAT 2.5 / WCAG 2.2 conformance summary (JSON)."""
        from audit.vpat import build_vpat
        if target_level not in ("A", "AA", "AAA"):
            raise HTTPException(400, "target_level must be A, AA, or AAA")
        result = database.get_audit_result(job_id)
        if not result:
            raise HTTPException(404, "Audit not found")
        return build_vpat(result, target_level=target_level)

    @app.get("/audit/{job_id}/vpat.html", response_class=HTMLResponse, tags=["audit"])
    def get_audit_vpat_html(job_id: str, target_level: str = "AA") -> HTMLResponse:
        """Stakeholder-facing VPAT rendered as standalone HTML."""
        from audit.vpat import render_vpat_html
        if target_level not in ("A", "AA", "AAA"):
            raise HTTPException(400, "target_level must be A, AA, or AAA")
        result = database.get_audit_result(job_id)
        if not result:
            raise HTTPException(404, "Audit not found")
        return HTMLResponse(render_vpat_html(result, target_level=target_level))

    @app.get("/audit/{job_id}/xlsx", tags=["audit"])
    def get_audit_xlsx(
        job_id: str,
        target_level: str = "AA",
        enrich: bool = False,
    ):
        """Download the audit as a formatted .xlsx workbook.

        When `enrich=true` the server calls OpenRouter before building
        the spreadsheet so the AI-generated location/reproduction/
        recommendation/user-impact fields populate. Requires
        OPENROUTER_API_KEY set in the worker environment; enrichment
        is silently skipped (columns remain populated from rule
        fallbacks) when the key is absent.
        """
        from fastapi.responses import Response
        from audit.export_xlsx import build_xlsx_bytes
        from audit.ai_enrich import enrich_issues

        if target_level not in ("A", "AA", "AAA"):
            raise HTTPException(400, "target_level must be A, AA, or AAA")
        result = database.get_audit_result(job_id)
        if not result:
            raise HTTPException(404, "Audit not found")

        if enrich:
            # Don't persist the enrichment back to the DB — enrichment is
            # re-runnable and we don't want to lock the audit record
            # into one model's framing.
            result = dict(result)
            result["issues"] = enrich_issues(result.get("issues") or [])

        xlsx = build_xlsx_bytes(result, target_level=target_level)
        # Strip everything but [A-Za-z0-9._-] before splicing into the
        # Content-Disposition filename — a job_id with CR/LF or quotes
        # would otherwise inject HTTP headers.
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "", job_id)[:64] or "audit"
        filename = f"audit-{safe_id}.xlsx"
        return Response(
            content=xlsx,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

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

        if request.url is None:
            raise HTTPException(422, "/audit/quick accepts exactly one `url`")
        url = request.url
        options = request.options.model_dump(mode="json")

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

    @app.post("/announce", tags=["audit"])
    async def announce_preview(request: AuditRequest) -> dict:
        """Announcement preview — the computed name / role / state a screen
        reader reads for each element, from Chromium's accessibility tree
        (no NVDA required).

        Deterministic and fast: the discrete name/role/state fields are
        authoritative; the per-element `announcement` string is an
        APPROXIMATION of spoken output, not verbatim NVDA speech (run the
        NVDA worker for that). Same SSRF / input validation and wall-clock
        timeout as /audit/quick.
        """
        from audit.announce import run_announcement_preview

        if request.url is None:
            raise HTTPException(422, "/announce accepts exactly one `url`")
        url = request.url
        options = request.options.model_dump(mode="json")

        try:
            return await asyncio.wait_for(
                run_in_threadpool(run_announcement_preview, url, options),
                timeout=_QUICK_AUDIT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            rid = current_request_id() or "-"
            log.warning("announce preview timed out after %ss (request_id=%s)",
                        _QUICK_AUDIT_TIMEOUT_SECONDS, rid)
            raise HTTPException(
                504,
                f"Announcement preview timed out after "
                f"{_QUICK_AUDIT_TIMEOUT_SECONDS}s. Request ID: {rid}",
            )
        except Exception:
            rid = current_request_id() or "-"
            log.exception("announce preview failed (request_id=%s)", rid)
            raise HTTPException(500, f"Announcement preview failed. Request ID: {rid}")

    return app
