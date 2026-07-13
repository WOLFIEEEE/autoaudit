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
    accessible_auth,
    aria,
    char_key_shortcuts,
    cognitive,
    color_only,
    consistent_help,
    dragging,
    dynamic,
    error_flow,
    fake_button,
    focus_obscured,
    forms,
    hover_focus,
    keyboard,
    lang_detection,
    live_regions,
    media,
    mobile,
    plugins as _plugins,
    preferences,
    redundant_entry,
    reflow,
    reveal,
    responsive,
    skiplinks,
    structure,
    target_size,
    timing,
    visual,
    vlm,
    wcag_coverage,
    wcag_engine,
    widgets,
)
from audit._fingerprint import fingerprint_for_issue
from audit.rule_versions import (
    RULE_SET_META_VERSION,
    rule_set_hash as _rule_set_hash,
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
        # Per-page collectors that participate in cross-page analysis
        # (3.2.6 Consistent Help today; future cross-page rules will
        # push their snapshots here too). Keyed by URL because the
        # multi-page loop reuses self.results across iterations.
        self._consistent_help_per_page: list[tuple[str, dict[str, Any]]] = []
        # Optional snapshot capture. When `options["snapshot"]` is set,
        # every page's compressed DOM + metadata is recorded so users
        # can re-run a future rule set against the historical state.
        # Keyed by URL; populated in `_audit_one`.
        self._snapshots: dict[str, dict[str, Any]] = {}

    def _resolve_skip_nvda(self) -> bool:
        explicit = self.options.get("skip_nvda")
        if explicit is None:
            return CONFIG.default_skip_nvda
        return bool(explicit)

    def run(self) -> dict[str, Any]:
        start = time.time()
        # Exposed to _run_module so it can compute elapsed time against
        # the overall-audit wall clock (not just per-module).
        self._orch_start = start
        headless = bool(self.options.get("headless", True))

        if len(self.urls) == 1:
            with open_page(self.urls[0], self.options, headless=headless) as page:
                self._audit_one(page)
                # Collect + dedup FIRST so annotations attach to the
                # canonical issue records (not the pre-dedup duplicates
                # axe produces).
                all_issues = self._collect_issues()
                all_issues = deduplicate_issues(all_issues)
                if self.options.get("screenshots"):
                    from audit import screenshots as _shots
                    _shots.annotate_issues(page, all_issues)
            summary = calculate_scores(all_issues)
            return {
                "url": self.urls[0],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "duration_seconds": round(time.time() - start, 2),
                "summary": summary,
                "issues": all_issues,
                "modules": self._module_summaries(),
                "nvda_status": self._initial_nvda_status(),
                "wcag_coverage": wcag_coverage.report(
                    target_level=str(self.options.get("level", "aa")).upper()
                ),
                # Reproducibility stamp: a run with the same rule_set_hash
                # will produce the same rule-firings on the same DOM.
                # Auditors verifying a historical VPAT compare hashes
                # rather than trying to reconstruct the rule logic.
                "rule_set_meta_version": RULE_SET_META_VERSION,
                "rule_set_hash": _rule_set_hash(),
                "snapshots": self._snapshots if self._snapshots else None,
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

                # Reset per-page state. `self.url` is the URL currently
                # being audited — modules and per-page collectors
                # consult it to label their findings.
                self.results = {}
                self.url = target
                self._audit_one(mgr.page)
                per_page_issues = self._collect_issues()
                per_page_issues = deduplicate_issues(per_page_issues)

                # Namespace issue IDs by URL so two pages flagging the
                # same rule don't collide in the aggregated `issues`
                # list. Fingerprint is left as-is (element-level) so
                # the cross-page grouping pass below can detect
                # "same defect across 23/25 pages" — a design-system
                # signal that re-deduping would erase.
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

        # Cross-page groups: collapse "same (rule, element-fingerprint)
        # on multiple pages" into a single group record. This is the
        # signal design-system owners care about — a single component
        # fix remediates every instance. Computed BEFORE the aggregated
        # dedup so we still see every repetition.
        cross_page_groups = _group_across_pages(aggregated_issues)

        # WCAG 3.2.6 Consistent Help — only meaningful on multi-page
        # audits. Issues are appended to the aggregated list so they
        # show up alongside other findings; pages_out's per-page
        # arrays don't include them (the violation is *between* pages,
        # not on any single page).
        ch_issues = consistent_help.analyze_cross_page(
            self._consistent_help_per_page
        )
        for issue in ch_issues:
            issue.setdefault("page_url", issue.get("details", {}).get("page_url"))
        aggregated_issues.extend(ch_issues)

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
            "cross_page_groups": cross_page_groups,
            "modules": aggregated_modules,
            "nvda_status": nvda_status,
            "wcag_coverage": wcag_coverage.report(
                target_level=str(self.options.get("level", "aa")).upper()
            ),
            "rule_set_meta_version": RULE_SET_META_VERSION,
            "rule_set_hash": _rule_set_hash(),
            "snapshots": self._snapshots if self._snapshots else None,
        }

    def _audit_one(self, page) -> None:
        """Run every module against a single page. Fills `self.results`."""
        self._capture_snapshot(page)
        self._run_static_analysis(page)
        self._run_module("visual", lambda: visual.run(page, self.options))
        self._run_module("responsive", lambda: responsive.run(page, self.options))
        # Reflow runs BEFORE the keyboard walk because it resizes the
        # viewport. It restores the viewport on exit, but keeping it
        # near the start of the pipeline keeps the blast radius small
        # if the restore ever breaks.
        self._run_module("reflow", lambda: reflow.run(page, self.options))
        self._run_module("mobile", lambda: mobile.run(page, self.options))
        # Pixel analysis is slow. Only run when explicitly opted in.
        if self.options.get("pixel_analysis"):
            from audit import pixels as _pixels
            self._run_module("pixels_contrast", lambda: _pixels.run_contrast(page))
        self._run_module("keyboard", lambda: keyboard.run(page, None, self.options))
        self._run_module("forms", lambda: forms.run(page, self.options))
        self._run_module("widgets", lambda: widgets.run(page, self.options))
        # WCAG 2.5.8 Target Size (Minimum) — single-page rule, runs
        # against the rendered DOM. Inline + spacing exceptions are
        # detected; equivalent / essential exceptions are not.
        self._run_module("target_size", lambda: target_size.run(page, self.options))
        # WCAG 4.1.3 Status Messages — passive enumeration of live
        # regions and detection of mis-configurations (role/aria-live
        # conflicts, silenced regions, empty regions on load).
        self._run_module("live_regions", lambda: live_regions.run(page, self.options))
        # WCAG 1.4.1 Use of Color — color-only-signifier heuristic.
        # Low-confidence by design; surfaced for human review.
        self._run_module("color_only", lambda: color_only.run(page, self.options))
        # WCAG 2.4.1 Bypass Blocks — verifies skip links *work*, not
        # just that one exists. Activates each candidate and asserts
        # focus moves into the declared target.
        self._run_module("skiplinks", lambda: skiplinks.run(page, self.options))
        # WCAG 2.1.4 Character Key Shortcuts — single-char accesskeys +
        # unguarded single-key inline handlers. Detects a high-precision
        # subset; turn-off/remap/focus-scope still needs manual review.
        self._run_module(
            "char_key_shortcuts",
            lambda: char_key_shortcuts.run(page, self.options),
        )
        # WCAG 2.2.1 Timing Adjustable — client-side <meta refresh> time
        # limits / timed redirects. Server-side session timeouts remain
        # out of scope (invisible to a page audit).
        self._run_module("timing", lambda: timing.run(page, self.options))
        # WCAG 2.1.1 / 4.1.2 Fake buttons — div/span styled as a control
        # (cursor:pointer) but not keyboard-focusable and role-less. The
        # keyboard walk can't see these (they're unreachable), so this
        # positive heuristic is the only module that catches them.
        self._run_module("fake_button", lambda: fake_button.run(page, self.options))
        # WCAG 3.1.1 lang vs content — declared lang attribute against
        # the dominant Unicode script of body text. Catches templates
        # that ship lang="en" before localisation.
        self._run_module(
            "lang_detection",
            lambda: lang_detection.run(page, self.options),
        )
        # WCAG 2.5.7 Dragging Movements — single-pointer alternative
        # required (new in WCAG 2.2 AA).
        self._run_module("dragging", lambda: dragging.run(page, self.options))
        # WCAG 3.3.7 Redundant Entry — single-page heuristic;
        # full multi-step state-tracking is deferred.
        self._run_module(
            "redundant_entry",
            lambda: redundant_entry.run(page, self.options),
        )
        # WCAG 3.3.8 Accessible Authentication — CAPTCHA + cognitive
        # function test detection on auth-context pages only.
        self._run_module(
            "accessible_auth",
            lambda: accessible_auth.run(page, self.options),
        )
        # WCAG 1.4.13 Content on Hover/Focus — dismissibility +
        # hoverability checks on tooltip-style triggers.
        self._run_module("hover_focus", lambda: hover_focus.run(page, self.options))
        # WCAG 4.1.3 / 3.3.1 — submits each non-destructive form to
        # verify error messages are SR-announceable. Opt-in via
        # options["error_flow_check"] because submitting forms has
        # side effects.
        self._run_module("error_flow", lambda: error_flow.run(page, self.options))
        # WCAG 2.4.11 Focus Not Obscured — runs AFTER the keyboard
        # walk because it consumes the tab-stop list.
        kb_stops_for_obscure = (self.results.get("keyboard") or {}).get("tab_stops") or []
        self._run_module(
            "focus_obscured",
            lambda: focus_obscured.run(page, kb_stops_for_obscure, self.options),
        )
        # Third-party plugins via setuptools entry points. Each plugin's
        # result is namespaced under `plugin.<name>` so issues fold
        # into dedup, grouping, and the report alongside built-ins.
        for plugin_name, plugin_result in _plugins.run_plugins(page, self.options).items():
            self.results[plugin_name] = plugin_result
        # WCAG 3.2.6 collector — single-page detection only at this
        # stage. The cross-page comparison runs once every URL has
        # been audited (multi-URL flow only).
        ch_collected = consistent_help.collect(page)
        self.results["consistent_help_collector"] = ch_collected
        self._consistent_help_per_page.append((self.url, ch_collected))
        self._run_module("preferences", lambda: preferences.run(page, self.options))

        from audit import screen_reader

        self._run_module("screen_reader", lambda: screen_reader.run(page, self.options))

        # Pixel-level focus-indicator pass. Runs AFTER the keyboard
        # walk so we already have the tab-stop list. Opt-in because
        # screenshotting each stop twice adds ~0.2s/element.
        if self.options.get("pixel_analysis"):
            from audit import pixels as _pixels
            kb_stops_for_focus = (self.results.get("keyboard") or {}).get("tab_stops") or []
            self._run_module(
                "pixels_focus",
                lambda: _pixels.run_focus(page, kb_stops_for_focus),
            )

        # WCAG 2.5.3 Label in Name (level A) runs off the keyboard walk's
        # tab-stop data (it needs both visible_text and accessible_name
        # per element). We layer its issues into screen_reader.issues so
        # they group with other SR rules in the report, and because 2.5.3
        # is fundamentally a screen-reader / speech-input concern.
        kb_stops = (self.results.get("keyboard") or {}).get("tab_stops") or []
        if kb_stops:
            sr_bucket = self.results.setdefault("screen_reader", {})
            sr_bucket.setdefault("issues", [])
            sr_bucket["issues"].extend(screen_reader.analyze_label_in_name(kb_stops))

        # User-defined YAML rules run alongside static analysis. Path
        # comes from options["yaml_rules"] or env var AUTOAUDIT_YAML_RULES.
        # We load-and-run lazily so missing optional dependency
        # (pyyaml) never sinks the audit.
        yaml_path = (
            self.options.get("yaml_rules")
            or __import__("os").environ.get("AUTOAUDIT_YAML_RULES")
        )
        if yaml_path:
            self._run_module(
                "yaml_rules",
                lambda: self._run_yaml_rules(page, yaml_path),
            )

        # Interaction reveal. Layer 1 (disclosure aria-expanded state,
        # WCAG 4.1.2) is read-only and always runs. Layer 2 actuation
        # (click toggles, measure revealed controls) only runs when
        # options["reveal"] is set, because it mutates the DOM — so it
        # sits late in the pipeline, just before the dynamic block.
        self._run_module("reveal", lambda: reveal.run(page, self.options))

        # VLM-judged semantic checks (opt-in via options["vlm_checks"]
        # plus a configured OPENROUTER_API_KEY). Runs against the initial
        # render — must be before dynamic interactions mutate the DOM.
        self._run_module("vlm", lambda: vlm.run(page, self.options))

        # Dynamic-state interactions run LAST because each interaction
        # may leave the page in a mutated state (modals open, form
        # fields filled). Static modules expect the initial render;
        # running them after dynamic would produce noisy diffs.
        if self.options.get("interactions"):
            self._run_module("dynamic", lambda: dynamic.run(page, self.options))

        # Mobile-viewport pass. A lot of UI (hamburger menus, off-canvas
        # nav, mobile-only controls) is display:none at desktop width, so
        # the desktop pipeline above structurally cannot see it. Re-run
        # the viewport-sensitive modules at a phone viewport and merge any
        # NEW findings (the deduplicator collapses anything identical to a
        # desktop finding by fingerprint). On by default; disable with
        # options["mobile_pass"] = False.
        self._run_mobile_pass(page)

        # Path B inline: only when we're already on Windows. Otherwise
        # the task layer enqueues `audit.run_nvda` to a Windows worker.
        if (
            not self._resolve_skip_nvda()
            and platform.system() == "Windows"
            and self.results.get("screen_reader", {}).get("ran")
        ):
            self._run_nvda_inline(page)

    # Phone viewport for the mobile pass. iPhone-class logical width;
    # narrow enough to trip the responsive breakpoints that hide the
    # desktop nav and show the hamburger.
    MOBILE_VIEWPORT = {"width": 390, "height": 844}
    # Modules re-run at mobile width. Curated to the ones whose findings
    # depend on layout / visibility (so we don't pay to re-run viewport-
    # independent checks like contrast, alt text, or heading structure).
    # `reveal` runs with actuation so the opened mobile menu is audited.
    MOBILE_PASS_MODULES = ("keyboard", "fake_button", "target_size", "reveal")

    def _should_run_mobile_pass(self) -> bool:
        """True when the mobile pass should run for this audit.

        Off when explicitly disabled, or when the audit is already at a
        mobile-class viewport (re-running the same width would only
        produce duplicate findings the deduplicator throws away).
        """
        if not self.options.get("mobile_pass", True):
            return False
        vp = self.options.get("viewport") or {}
        try:
            if vp.get("width") and int(vp["width"]) <= 600:
                return False
        except (TypeError, ValueError):
            pass
        return True

    def _run_mobile_pass(self, page) -> None:
        """Resize to a phone viewport, re-run viewport-sensitive modules,
        tag any findings as mobile-only, and restore the viewport.

        Findings flow into `self.results` under `mobile:<module>` keys so
        `_collect_issues` picks them up and the deduplicator merges any
        that duplicate a desktop finding (same rule + element fingerprint).
        Mobile-only findings — the hamburger menu's missing state, an
        unnamed off-canvas control — survive deduplication and carry a
        `viewport: "mobile"` tag.
        """
        if not self._should_run_mobile_pass():
            return
        try:
            original_vp = page.viewport_size or {"width": 1280, "height": 720}
        except Exception:  # pragma: no cover - defensive
            original_vp = {"width": 1280, "height": 720}
        try:
            page.set_viewport_size(self.MOBILE_VIEWPORT)
            # Let responsive CSS / JS (media queries, resize handlers)
            # settle before re-probing.
            page.wait_for_timeout(350)
        except Exception:
            log.debug("mobile pass: viewport resize failed; skipping", exc_info=True)
            return

        try:
            mobile_opts = dict(self.options)
            mobile_opts["reveal"] = True  # actuate the mobile menu
            runners = {
                "keyboard": lambda: keyboard.run(page, None, mobile_opts),
                "fake_button": lambda: fake_button.run(page, mobile_opts),
                "target_size": lambda: target_size.run(page, mobile_opts),
                "reveal": lambda: reveal.run(page, mobile_opts),
            }
            for name in self.MOBILE_PASS_MODULES:
                key = f"mobile:{name}"
                self._run_module(key, runners[name])
                result = self.results.get(key) or {}
                for issue in result.get("issues") or []:
                    issue["viewport"] = "mobile"
                    issue["id"] = f"mobile-{issue.get('id', '')}"
                    issue.setdefault("details", {})["viewport_width"] = (
                        self.MOBILE_VIEWPORT["width"]
                    )
        finally:
            try:
                page.set_viewport_size(original_vp)
                page.wait_for_timeout(120)
            except Exception:  # pragma: no cover - defensive
                log.debug("mobile pass: viewport restore failed", exc_info=True)

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

    def _run_yaml_rules(self, page, path: str) -> dict[str, Any]:
        """Load and execute YAML rules. Returns a module-shaped result.

        When AUTOAUDIT_YAML_RULES_ROOT is set, the resolved path must
        live inside that directory. Symlinks are followed before the
        check, so a symlink farm under the root cannot escape.
        """
        import os as _os
        import time as _t
        from pathlib import Path as _Path

        from audit import rules_yaml

        started = _t.time()
        try:
            resolved = _Path(path).resolve(strict=True)
            root = _os.environ.get("AUTOAUDIT_YAML_RULES_ROOT")
            if root:
                root_resolved = _Path(root).resolve(strict=True)
                # is_relative_to is 3.9+; this codebase already uses 3.10+ syntax.
                if not resolved.is_relative_to(root_resolved):
                    raise ValueError(
                        f"yaml_rules path {resolved} escapes "
                        f"AUTOAUDIT_YAML_RULES_ROOT {root_resolved}"
                    )
            rules = rules_yaml.load_rules(resolved)
        except Exception as exc:
            return {
                "ran": False,
                "error": f"rules load failed: {exc}",
                "issues": [],
                "duration_seconds": round(_t.time() - started, 3),
            }
        issues = rules_yaml.run(page, rules)
        return {
            "ran": True,
            "issues": issues,
            "rule_count": len(rules),
            "duration_seconds": round(_t.time() - started, 3),
        }

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

    # Default per-module soft budget in seconds. A module that exceeds
    # this produces a "slow" annotation in the report but isn't killed —
    # Playwright's sync API runs greenlet-style and doesn't support
    # external interruption. Caller can override via
    # options["module_budget_seconds"].
    DEFAULT_MODULE_BUDGET_S = 30.0

    def _run_module(self, name: str, fn) -> None:
        """Execute a module and trap any exception into an error result.

        Without this, a crash in (say) `visual.run()` skips every later
        module and raises out of the `with open_page(...)` block, which
        would otherwise prevent the remaining modules from running and
        obscure the real failure. We want best-effort completion: each
        module's failure is isolated and surfaced in the report.

        Also enforces a soft budget: if the overall audit has been
        running longer than `options["overall_budget_seconds"]`, the
        module is skipped with a `budget_exceeded: True` marker so
        operators can see what was cut.
        """
        overall_budget = float(
            self.options.get("overall_budget_seconds") or 0
        )
        if overall_budget > 0 and hasattr(self, "_orch_start"):
            elapsed = time.time() - self._orch_start
            if elapsed > overall_budget:
                log.warning(
                    "skipping module %s — overall budget %.0fs exceeded (elapsed %.1fs)",
                    name, overall_budget, elapsed,
                )
                self.results[name] = {
                    "ran": False,
                    "budget_exceeded": True,
                    "skipped": True,
                    "issues": [],
                }
                return

        module_start = time.time()
        try:
            self.results[name] = fn()
        except Exception as exc:
            log.exception("module %s failed", name)
            self.results[name] = {
                "ran": False,
                "error": str(exc),
                "issues": [],
            }
            return

        mod_budget = float(
            self.options.get("module_budget_seconds")
            or self.DEFAULT_MODULE_BUDGET_S
        )
        elapsed = time.time() - module_start
        if mod_budget > 0 and elapsed > mod_budget:
            # Surface the overrun so operators can tune or investigate.
            log.warning(
                "module %s exceeded soft budget: %.1fs > %.1fs",
                name, elapsed, mod_budget,
            )
            self.results[name].setdefault("warnings", []).append(
                f"exceeded {mod_budget:.0f}s soft budget (took {elapsed:.1f}s)"
            )

    def _capture_snapshot(self, page) -> None:
        """When the `snapshot` option is set, record a portable
        per-page DOM snapshot so the audit can be re-run against the
        historical state with a future rule set.

        The snapshot is intentionally minimal: outer HTML, declared
        viewport, the `User-Agent`, the URL, and a timestamp. We do
        not capture screenshots or computed-style trees here — the
        existing `screenshots` option is the right home for those,
        and storing every node's computed style would balloon to
        many MB per page.
        """
        if not self.options.get("snapshot"):
            return
        try:
            snap = page.evaluate(r"""() => ({
                outer_html: document.documentElement.outerHTML.slice(0, 2_000_000),
                base_uri: document.baseURI || '',
                user_agent: navigator.userAgent || '',
                viewport: {
                    w: window.innerWidth,
                    h: window.innerHeight,
                },
            })""")
        except Exception as exc:
            log.debug("snapshot capture failed: %s", exc)
            return
        snap["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        snap["url"] = self.url
        snap["rule_set_hash"] = _rule_set_hash()
        self._snapshots[self.url] = snap

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
# Cross-page grouping.


def _group_across_pages(
    aggregated_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse issues that share (rule, element-fingerprint) across pages.

    The aggregated list contains one entry per page per rule per
    element. Grouping by (rule, fingerprint) reveals the design-system
    signal: "this defect appears on 23 of 25 pages" — one component
    fix remediates every instance.

    Issues without a fingerprint (legacy raw dicts) are grouped only
    on exact-rule identity, which is coarser but still useful. Each
    group carries `pages_affected` for the report headline.
    """
    groups: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []

    for issue in aggregated_issues:
        rule = issue.get("rule", "")
        fp = issue.get("fingerprint")
        if not fp:
            fp = fingerprint_for_issue(issue)
        key = (rule, fp)

        if key not in groups:
            groups[key] = {
                "rule": rule,
                "fingerprint": fp,
                "severity": issue.get("severity"),
                "principle": issue.get("principle"),
                "level": issue.get("level"),
                "wcag_criteria": issue.get("wcag_criteria") or [],
                "title": issue.get("title", ""),
                "pages_affected": [],
                "instance_count": 0,
                "example_selector": (issue.get("element") or {}).get("selector", ""),
            }
            order.append(key)
        g = groups[key]
        g["instance_count"] += 1
        page_url = issue.get("page_url")
        if page_url and page_url not in g["pages_affected"]:
            g["pages_affected"].append(page_url)

    # Sort groups: first by severity, then by pages-affected count
    # descending so "present on every page" rises to the top.
    rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    return sorted(
        [groups[k] for k in order],
        key=lambda g: (
            rank.get(g.get("severity", "minor"), 4),
            -len(g.get("pages_affected") or []),
        ),
    )


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

    # Windows path. We walk the page once with keyboard focus (reusing
    # the existing keyboard probe for selector/name data) while NVDA is
    # capturing. Path A always ran inline during the main audit; this
    # function layers the real-screen-reader observations on top.
    from audit import keyboard as keyboard_module  # local import avoids cycles

    nvda = screen_reader.NVDAController()
    try:
        nvda.ensure_running()
    except (screen_reader.NVDAUnavailableError, NotImplementedError) as exc:
        return {
            "nvda_status": "skipped",
            "nvda": {"ran": False, "skipped": True, "reason": str(exc)},
            "issues": [],
            "duration_seconds": round(time.time() - start, 2),
        }

    # Path B needs a slow tab walk. Default wait_ms=50 is tuned for
    # the static keyboard analyzer; NVDA's speech pipeline needs
    # roughly 300ms to settle before we can attribute an utterance
    # to the current tab stop without time-window drift.
    walk_options = dict(options)
    walk_options.setdefault("wait_ms", int(nvda.PER_STOP_SPEECH_WAIT * 1000))

    try:
        with open_page(url, options, headless=bool(options.get("headless", True))) as page:
            # Let NVDA finish its page-load preamble (window title, region,
            # document role) BEFORE we start capturing. Otherwise those
            # utterances end up aligned with the first tab stops and the
            # mismatch detector fires false positives.
            time.sleep(2.0)
            nvda.start_capture()
            stops, _cycled = keyboard_module._walk(page, walk_options)  # noqa: SLF001
            # Small tail-sleep so the final utterance lands in the log
            # before we close the capture window.
            time.sleep(nvda.PER_STOP_SPEECH_WAIT)
            nvda.stop_capture()
            nvda_result = nvda.analyze_results(stops)

            # Browse-mode pass — how most SR users actually read. Down-
            # arrow walks through every "line" of content (not just
            # focusable controls), so we catch text that's visible but
            # skipped by screen readers, and aria-hidden content that
            # leaks. Wrapped in its own try so a browse-mode failure
            # doesn't invalidate the tab-walk results.
            try:
                browse = nvda.run_browse_mode(page)
                browse_issues = screen_reader.analyze_browse_mode(browse)
                nvda_result.setdefault("browse_mode", {}).update(
                    {
                        "ran": browse.get("ran", False),
                        "utterances": len(browse.get("utterances") or []),
                        "visible_nodes": len(browse.get("visible_text_nodes") or []),
                        "log_bytes": browse.get("log_bytes", 0),
                    }
                )
                # Append issues so both tab-walk and browse-mode rules
                # flow through the same dedup / scoring pass.
                nvda_result.setdefault("issues", []).extend(browse_issues)
            except Exception:
                log.exception("browse-mode analysis failed; tab-walk results kept")
                nvda_result.setdefault("browse_mode", {"ran": False, "error": True})
    except Exception as exc:
        log.exception("NVDA follow-up failed for %s", url)
        return {
            "nvda_status": "failed",
            "nvda": {"ran": False, "error": str(exc)},
            "issues": [],
            "duration_seconds": round(time.time() - start, 2),
        }
    finally:
        nvda.shutdown()

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
