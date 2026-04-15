"""Merge duplicate issues within and across modules.

Two issues are treated as duplicates when they describe the same DOM
element (same selector) and the same rule. Higher severity wins; on
ties, first seen wins.

We deliberately do NOT merge different rules on the same element.
Early versions keyed by rule prefix ("forms-*" all root to "forms"),
which accidentally collapsed e.g. forms-input-no-label with
forms-aria-invalid-no-description on the same input — two distinct
problems. Cross-module overlap (axe's `label` vs our
`forms-input-no-label`) is better handled by a targeted mapping or
by the UI layer grouping by WCAG criterion.
"""

from __future__ import annotations

from typing import Any

SEVERITY_RANK = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}


def _key(issue: dict[str, Any]) -> str:
    selector = (issue.get("element") or {}).get("selector", "")
    rule = issue.get("rule", "")
    return f"{selector}::{rule}"


def deduplicate_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for issue in issues:
        k = _key(issue)
        if k not in best_by_key:
            best_by_key[k] = issue
            order.append(k)
            continue
        existing = best_by_key[k]
        new_rank = SEVERITY_RANK.get(issue.get("severity", "minor"), 9)
        old_rank = SEVERITY_RANK.get(existing.get("severity", "minor"), 9)
        if new_rank < old_rank:
            best_by_key[k] = issue

    return [best_by_key[k] for k in order]
