"""Audit orchestrator: runs every enabled module and aggregates results.

Currently only `wcag_engine` produces real issues; the rest are stubs that
return empty module results. The orchestrator is nevertheless wired end-to-end
so that filling in a module is a drop-in change.

robots.txt: not checked. Not parsed. Not imported. See README.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from audit import (
    aria,
    cognitive,
    forms,
    keyboard,
    media,
    responsive,
    structure,
    visual,
    wcag_engine,
)
from audit.browser import open_page
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
    def __init__(self, url: str, options: dict[str, Any]):
        self.url = url
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
        with open_page(self.url, self.options, headless=headless) as page:
            self._run_static_analysis(page)
            self.results["visual"] = visual.run(page, self.options)
            self.results["responsive"] = responsive.run(page, self.options)
            self.results["keyboard"] = keyboard.run(page, None, self.options)
            self.results["forms"] = forms.run(page, self.options)

            if self._resolve_skip_nvda():
                self.results["screen_reader"] = {
                    "ran": False,
                    "skipped": True,
                    "reason": "skip_nvda enabled or non-Windows host",
                    "issues": [],
                }
            else:
                from audit.screen_reader import NVDAController, NVDAUnavailableError

                try:
                    nvda = NVDAController()
                    nvda.ensure_running()
                    # Real NVDA flow lives here; not implemented yet.
                    self.results["screen_reader"] = nvda.analyze_results([])
                except (NVDAUnavailableError, NotImplementedError) as exc:
                    self.results["screen_reader"] = {
                        "ran": False,
                        "skipped": True,
                        "reason": str(exc),
                        "issues": [],
                    }

        all_issues = self._collect_issues()
        all_issues = deduplicate_issues(all_issues)
        summary = calculate_scores(all_issues)

        return {
            "url": self.url,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": round(time.time() - start, 2),
            "summary": summary,
            "issues": all_issues,
            "modules": self._module_summaries(),
        }

    def _run_static_analysis(self, page) -> None:
        with ThreadPoolExecutor(max_workers=len(STATIC_MODULES)) as pool:
            futures = {
                pool.submit(mod.run, page, self.options): name
                for name, mod in STATIC_MODULES.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    self.results[name] = future.result()
                except Exception as exc:
                    log.exception("module %s failed", name)
                    self.results[name] = {
                        "ran": False,
                        "error": str(exc),
                        "issues": [],
                    }

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
