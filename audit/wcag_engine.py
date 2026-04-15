"""axe-core WCAG rule engine.

Injects axe-core into the loaded page and runs it. Normalizes the result
into the project's common `issue` shape.

axe-core is loaded from a bundled vendor/axe.min.js when present, otherwise
from the configured CDN. Run scripts/fetch_axe.py to vendor it locally
(recommended for production / air-gapped environments).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from server.config import CONFIG
from audit._wcag import principle_for

log = logging.getLogger(__name__)

# Map axe severity → our severity vocabulary.
AXE_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "serious": "serious",
    "moderate": "moderate",
    "minor": "minor",
}


def _principle_from_tags(tags: list[str]) -> str:
    """Derive the WCAG principle from axe's tag list.

    axe emits tags like 'wcag143' meaning 1.4.3. We extract the leading
    digit(s) and defer to the shared principle_for helper so axe-sourced
    issues use the same mapping as custom-rule issues.
    """
    criteria = _wcag_criteria_from_tags(tags)
    return principle_for(criteria)


def _wcag_criteria_from_tags(tags: list[str]) -> list[str]:
    # axe emits tags like 'wcag143' meaning WCAG 1.4.3.
    criteria: list[str] = []
    for tag in tags:
        if tag.startswith("wcag") and len(tag) > 4 and tag[4:].isdigit():
            digits = tag[4:]
            if len(digits) == 3:
                criteria.append(f"{digits[0]}.{digits[1]}.{digits[2]}")
            elif len(digits) == 4:
                criteria.append(f"{digits[0]}.{digits[1]}.{digits[2:]}")
    return criteria


def _inject_axe(page) -> None:
    """Inject axe-core into the page. Prefer local vendor, fall back to CDN."""
    vendor_path = CONFIG.axe_script_path
    if os.path.exists(vendor_path):
        page.add_script_tag(path=vendor_path)
        return
    log.info("axe vendor not found at %s; loading from CDN %s", vendor_path, CONFIG.axe_cdn_url)
    page.add_script_tag(url=CONFIG.axe_cdn_url)


def _normalize_violation(v: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    tags = v.get("tags", [])
    principle = _principle_from_tags(tags)
    wcag = _wcag_criteria_from_tags(tags)
    severity = AXE_SEVERITY.get((v.get("impact") or "minor").lower(), "minor")

    for idx, node in enumerate(v.get("nodes", [])):
        target = node.get("target") or []
        selector = target[0] if isinstance(target, list) and target else ""
        issues.append(
            {
                "id": f"axe-{v.get('id', 'unknown')}-{idx}",
                "module": "wcag_engine",
                "rule": v.get("id", "unknown"),
                "severity": severity,
                "principle": principle,
                "wcag_criteria": wcag,
                "title": v.get("help", v.get("id", "WCAG violation")),
                "description": node.get("failureSummary") or v.get("description", ""),
                "element": {
                    "selector": selector,
                    "html_snippet": node.get("html", ""),
                },
                "details": {
                    "help_url": v.get("helpUrl", ""),
                    "axe_tags": tags,
                    "impact": v.get("impact"),
                },
                "fix_suggestion": node.get("failureSummary", ""),
            }
        )
    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run axe-core against `page` and return a module result dict."""
    options = options or {}
    start = time.time()
    try:
        _inject_axe(page)
    except Exception as exc:
        log.exception("axe-core injection failed")
        return {
            "ran": False,
            "error": f"axe injection failed: {exc}",
            "issues": [],
            "duration_seconds": round(time.time() - start, 2),
        }

    level = options.get("level", "aa")
    runonly_tags = {"a": ["wcag2a", "wcag21a", "wcag22a"],
                    "aa": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
                    "aaa": ["wcag2a", "wcag2aa", "wcag2aaa", "wcag21aa", "wcag21aaa", "wcag22aaa"]}
    tags = runonly_tags.get(level, runonly_tags["aa"])

    # axe.run returns a Promise; page.evaluate awaits it.
    axe_result = page.evaluate(
        """async (tags) => {
            return await axe.run(document, { runOnly: { type: 'tag', values: tags } });
        }""",
        tags,
    )

    issues: list[dict[str, Any]] = []
    for v in axe_result.get("violations", []):
        issues.extend(_normalize_violation(v))

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 2),
        "rules_checked": len(axe_result.get("passes", []))
        + len(axe_result.get("violations", []))
        + len(axe_result.get("incomplete", [])),
        "violations": len(axe_result.get("violations", [])),
        "passes": len(axe_result.get("passes", [])),
        "incomplete": len(axe_result.get("incomplete", [])),
    }
