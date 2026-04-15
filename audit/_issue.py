"""Helpers for building issue dicts in the shape the orchestrator expects.

Modules call `make_issue(...)` instead of hand-rolling dicts so that fields
like `wcag_criteria`, `element`, and `details` stay consistently typed.
"""

from __future__ import annotations

from typing import Any


def make_issue(
    *,
    issue_id: str,
    module: str,
    rule: str,
    severity: str,
    principle: str,
    wcag: list[str],
    title: str,
    description: str = "",
    selector: str = "",
    html_snippet: str = "",
    text: str = "",
    details: dict[str, Any] | None = None,
    fix: str = "",
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "module": module,
        "rule": rule,
        "severity": severity,
        "principle": principle,
        "wcag_criteria": wcag,
        "title": title,
        "description": description,
        "element": {
            "selector": selector,
            "html_snippet": html_snippet,
            "text_content": text,
        },
        "details": details or {},
        "fix_suggestion": fix,
    }
