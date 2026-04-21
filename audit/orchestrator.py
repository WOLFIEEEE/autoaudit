"""Audit orchestrator: runs every enabled module and aggregates results.

Two entry points:
- `AuditOrchestrator.run()` — full Path A audit (everything except real NVDA).
- `run_nvda_follow_up()`     — Path B only, appended to an existing audit.
  Invoked by the `audit.run_nvda` Celery task on the Windows worker.

robots.txt: not checked. Not parsed. Not imported. See README.
"""

from __future__ import annotations

import logging
import platform
import time
from typing import Any

from audit import (
    aria,
    cognitive,
    forms,
    keyboard,
    media,
    preferences,
    responsive,
    structure,
    visual,
    wcag_engine,
)
from audit.browser import open_browser, open_page
from audit.deduplicator import deduplicate_issues
from audit.scorer import calculate_scores
from server.config import CONFIG

log = logging.getLogger(__name__)

STATIC_MODULES = {
    "wcag_engine": wcag_engine,
    "structure": structure,
    "aria": aria,
    "media": media,
    "cognitive": cognitive,
}


class AuditOrchestrator:
    """Runs the full audit for one URL.

    Path B (real NVDA) is NOT invoked here on non-Windows platforms —
    instead, the Celery task layer enqueues `audit.run_nvda` to a
    Windows worker which reuses the same URL + options. The result
    saved here carries `nvda_status="pending"` in that case so polling
    clients know the report will grow.
    """

    def __init__(
        self,
        url: str | None = None,
        options: dict[str, Any] | None = None,
        *,
        urls: list[str] | None = None,
    ):
        """Construct a single- or multi-URL orchestrator.

        Exactly one of `url` or `urls` must be provided. The tests use
        the positional single-URL form; the FastAPI layer builds
        multi-URL runs from AuditRequest.target_urls().
        """
        if (url is None) == (not urls):
            raise ValueError("provide exactly one of `url` or `urls`")
        self.urls: list[str] = list(urls) if urls else [url]  # type: ignore[list-item]
        self.url: str = self.urls[0]
        self.options = options or {}
        self.results: dict[str, dict[str, Any]] = {}

    def _resolve_skip_nvda(self) -> bool:
        explicit = self.options.get("skip_nvda")
        if explicit is None:
            return CONFIG.default_skip_nvda
        return bool(explicit)

    def run(self) -> dict[str, Any]:
        start = time.time()
        headless = bool(self.options.get("headless", True))

        if len(self.urls) == 1:
            with open_page(self.urls[0], self.options, headless=headless) as page:
                self._audit_one(page)
            all_issues = self._collect_issues()
            all_issues = deduplicate_issues(all_issues)
            summary = calculate_scores(all_issues)
            return {
                "url": self.urls[0],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "duration_seconds": round(time.time() - start, 2),
                "summary": summary,
                "issues": all_issues,
                "modules": self._module_summaries(),
                "nvda_status": self._initial_nvda_status(),
            }

        # Multi-page: reuse one browser context across URLs so cookies /
        # login state persist between pages.
        return self._run_multi(start, headless)

    def _run_multi(self, start: float, headless: bool) -> dict[str, Any]:
        timeout_ms = int(self.options.get("timeout_seconds", 30)) * 1000
        pages_out: list[dict[str, Any]] = []
        aggregated_issues: list[dict[str, Any]] = []
        aggregated_modules: dict[str, dict[str, Any]] = {}
        any_nvda_pending = False

        with open_browser(self.urls[0], self.options, headless=headless) as mgr:
            for idx, target in enumerate(self.urls):
                if idx > 0:
                    try:
                        mgr.goto(target, timeout_ms=timeout_ms)
                    except Exception as exc:
                        log.exception("failed to navigate to %s", target)
                        pages_out.append(
                            {
                                "url": target,
                                "error": str(exc),
                                "issues": [],
                                "modules": {},
                            }
                        )
                        continue

                # Reset per-page state.
                self.results = {}
                self._audit_one(mgr.page)
                per_page_issues = self._collect_issues()
                per_page_issues = deduplicate_issues(per_page_issues)

                # Namespace issue IDs by URL so two pages flagging the
                # same rule don't collide at dedupe time.
                for issue in per_page_issues:
                    issue["page_url"] = target
                    issue["id"] = f"{target}|{issue['id']}"
                    aggregated_issues.append(issue)

                page_summary = calculate_scores(per_page_issues)
                page_modules = self._module_summaries()
                pages_out.append(
                    {
                        "url": target,
                        "summary": page_summary,
                        "issues": per_page_issues,
                        "modules": page_modules,
                    }
                )

                # Merge module summaries across pages (take max of issues).
                for mod_name, mod_data in page_modules.items():
                    existing = aggregated_modules.setdefault(mod_name, {
                        "ran": False,
                        "issues_found": 0,
                        "duration_seconds": 0.0,
                        "error": None,
                    })
                    existing["ran"] = existing["ran"] or mod_data.get("ran", False)
                    existing["issues_found"] += int(mod_data.get("issues_found", 0))
                    existing["duration_seconds"] += float(mod_data.get("duration_seconds", 0.0))
                    if mod_data.get("error") and not existing.get("error"):
                        existing["error"] = mod_data["error"]

                if self._initial_nvda_status() == "pending":
                    any_nvda_pending = True

        aggregated_issues = deduplicate_issues(aggregated_issues)
        rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
        aggregated_issues.sort(key=lambda i: rank.get(i.get("severity", "minor"), 4))
        aggregated_summary = calculate_scores(aggregated_issues)

        nvda_status = "pending" if any_nvda_pending else self._initial_nvda_status()

        return {
            "url": self.urls[0],  # Primary URL for compatibility.
            "urls": list(self.urls),
            "pages": pages_out,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": round(time.time() - start, 2),
            "summary": aggregated_summary,
            "issues": aggregated_issues,
            "modules": aggregated_modules,
            "nvda_status": nvda_status,
        }

    def _audit_one(self, page) -> None:
        """Run every module against a single page. Fills `self.results`."""
        self._run_static_analysis(page)
        self._run_module("visual", lambda: visual.run(page, self.options))
        self._run_module("responsive", lambda: responsive.run(page, self.options))
        self._run_module("keyboard", lambda: keyboard.run(page, None, self.options))
        self._run_module("forms", lambda: forms.run(page, self.options))
        self._run_module("preferences", lambda: preferences.run(page, self.options))

        from audit import screen_reader

        self._run_module("screen_reader", lambda: screen_reader.run(page, self.options))

        # Path B inline: only when we're already on Windows. Otherwise
        # the task layer enqueues `audit.run_nvda` to a Windows worker.
        if (
            not self._resolve_skip_nvda()
            and platform.system() == "Windows"
            and self.results.get("screen_reader", {}).get("ran")
        ):
            self._run_nvda_inline(page)

    def _initial_nvda_status(self) -> str:
        if self._resolve_skip_nvda():
            return "skipped"
        if platform.system() == "Windows":
            # Path B was (or will be) run inline, so no follow-up needed.
            sr = self.results.get("screen_reader", {})
            if sr.get("nvda", {}).get("ran"):
                return "completed"
            return "skipped"
        # Needs a Windows worker.
        return "pending"

    def _run_nvda_inline(self, page) -> None:
        from audit import screen_reader

        try:
            nvda = screen_reader.NVDAController()
            nvda.ensure_running()
            nvda_result = nvda.analyze_results([])
            nvda_issues = nvda_result.get("issues") or []
            self.results["screen_reader"].setdefault("issues", []).extend(nvda_issues)
            self.results["screen_reader"]["nvda"] = nvda_result
        except (screen_reader.NVDAUnavailableError, NotImplementedError) as exc:
            self.results["screen_reader"]["nvda"] = {
                "ran": False,
                "skipped": True,
                "reason": str(exc),
            }
        except Exception as exc:  # defensive
            log.exception("NVDA controller failed")
            self.results["screen_reader"]["nvda"] = {
                "ran": False,
                "error": str(exc),
            }

    def _run_module(self, name: str, fn) -> None:
        """Execute a module and trap any exception into an error result.

        Without this, a crash in (say) `visual.run()` skips every later
        module and raises out of the `with open_page(...)` block, which
        would otherwise prevent the remaining modules from running and
        obscure the real failure. We want best-effort completion: each
        module's failure is isolated and surfaced in the report.
        """
        try:
            self.results[name] = fn()
        except Exception as exc:
            log.exception("module %s failed", name)
            self.results[name] = {
                "ran": False,
                "error": str(exc),
                "issues": [],
            }

    def _run_static_analysis(self, page) -> None:
        # Run static modules sequentially. The plan calls for parallelism via
        # ThreadPoolExecutor, but Playwright's sync API is greenlet-based and
        # cannot be safely shared across threads — a parallel page.evaluate
        # from multiple threads raises `greenlet.error: cannot switch to a
        # different thread`. Each module's page.evaluate is fast (< 100ms
        # typical), so sequential is fine.
        for name, mod in STATIC_MODULES.items():
            self._run_module(name, lambda m=mod: m.run(page, self.options))

    def _collect_issues(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for module_name, result in self.results.items():
            for issue in result.get("issues") or []:
                issue.setdefault("module", module_name)
                issues.append(issue)
        rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
        issues.sort(key=lambda i: rank.get(i.get("severity", "minor"), 4))
        return issues

    def _module_summaries(self) -> dict[str, dict[str, Any]]:
        summaries: dict[str, dict[str, Any]] = {}
        for name, result in self.results.items():
            summaries[name] = {
                "ran": bool(result.get("ran", False)),
                "issues_found": len(result.get("issues") or []),
                "duration_seconds": float(result.get("duration_seconds", 0.0) or 0.0),
                "error": result.get("error"),
            }
        return summaries


# --------------------------------------------------------------------------
# Path B follow-up — runs on the Windows worker.


def run_nvda_follow_up(url: str, options: dict[str, Any]) -> dict[str, Any]:
    """Run Path B only against `url`, return a merge patch for the existing
    audit result.

    The returned dict is intended to be applied by the task layer:
    - `nvda`        dict with the raw NVDA result (transcript, tab stops, ...)
    - `issues`      new issues to append + re-dedupe
    - `nvda_status` final status ("completed", "skipped", "failed")

    This function is platform-checked: on non-Windows it returns a
    skipped patch rather than raising, so a misrouted task (CELERY_QUEUES
    misconfiguration) produces a clear signal rather than a crash loop.
    """
    from audit import screen_reader

    start = time.time()

    if platform.system() != "Windows":
        return {
            "nvda_status": "skipped",
            "nvda": {
                "ran": False,
                "skipped": True,
                "reason": "run_nvda_follow_up invoked on non-Windows platform",
            },
            "issues": [],
            "duration_seconds": round(time.time() - start, 2),
        }

    try:
        with open_page(url, options, headless=bool(options.get("headless", True))) as _page:
            nvda = screen_reader.NVDAController()
            nvda.ensure_running()
            # In a real NVDA implementation you'd walk tab stops from the
            # page and feed them to analyze_results. The stub accepts an
            # empty list and returns an empty issue set, preserving
            # end-to-end wiring.
            nvda_result = nvda.analyze_results([])
    except (screen_reader.NVDAUnavailableError, NotImplementedError) as exc:
        return {
            "nvda_status": "skipped",
            "nvda": {"ran": False, "skipped": True, "reason": str(exc)},
            "issues": [],
            "duration_seconds": round(time.time() - start, 2),
        }
    except Exception as exc:
        log.exception("NVDA follow-up failed for %s", url)
        return {
            "nvda_status": "failed",
            "nvda": {"ran": False, "error": str(exc)},
            "issues": [],
            "duration_seconds": round(time.time() - start, 2),
        }

    return {
        "nvda_status": "completed",
        "nvda": nvda_result,
        "issues": nvda_result.get("issues") or [],
        "duration_seconds": round(time.time() - start, 2),
    }


# --------------------------------------------------------------------------
# Quick / synchronous endpoint helper.


def run_quick_audit(url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synchronous, axe-core-only scan. Suitable for request-response endpoints."""
    options = dict(options or {})
    start = time.time()

    with open_page(url, options, headless=True) as page:
        axe_result = wcag_engine.run(page, options)

    issues = axe_result.get("issues", [])
    issues = deduplicate_issues(issues)
    summary = calculate_scores(issues)

    return {
        "url": url,
        "mode": "quick",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - start, 2),
        "summary": summary,
        "issues": issues,
        "modules": {
            "wcag_engine": {
                "ran": axe_result.get("ran", False),
                "issues_found": len(issues),
                "duration_seconds": axe_result.get("duration_seconds", 0.0),
                "error": axe_result.get("error"),
            }
        },
    }
