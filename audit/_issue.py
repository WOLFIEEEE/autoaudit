"""Helpers for building issue dicts in the shape the orchestrator expects.

Modules call `make_issue(...)` instead of hand-rolling dicts so that fields
like `wcag_criteria`, `element`, and `details` stay consistently typed.

`principle` is optional: when omitted, it's derived from the WCAG criteria
via audit._wcag.principle_for. Modules should not pass principle explicitly
unless they have a specific reason to override the derived value.
"""

from __future__ import annotations

from typing import Any

from audit._wcag import principle_for


def make_issue(
    *,
    issue_id: str,
    module: str,
    rule: str,
    severity: str,
    wcag: list[str],
    title: str,
    principle: str | None = None,
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
        "principle": principle if principle is not None else principle_for(wcag),
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
