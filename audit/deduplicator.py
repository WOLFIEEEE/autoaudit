"""Merge duplicate issues across modules.

Two issues are treated as duplicates when they describe the same DOM element
and the same root-cause rule family (rule prefix before the first hyphen).
The one with the higher severity wins; ties keep the first seen.
"""

from __future__ import annotations

from typing import Any

SEVERITY_RANK = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}


def _key(issue: dict[str, Any]) -> str:
    selector = (issue.get("element") or {}).get("selector", "")
    rule = issue.get("rule", "")
    rule_root = rule.split("-", 1)[0] if rule else ""
    return f"{selector}::{rule_root}"


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
