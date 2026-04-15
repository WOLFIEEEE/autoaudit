"""Score and grade an audit from its list of issues."""

from __future__ import annotations

from typing import Any

SEVERITY_PENALTY: dict[str, int] = {
    "critical": 8,
    "serious": 4,
    "moderate": 2,
    "minor": 1,
}

PRINCIPLES = ("perceivable", "operable", "understandable", "robust")


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def calculate_scores(issues: list[dict[str, Any]]) -> dict[str, Any]:
    total_penalty = sum(SEVERITY_PENALTY.get(i.get("severity", "minor"), 1) for i in issues)
    overall = max(0, 100 - total_penalty)

    by_principle: dict[str, dict[str, int]] = {}
    for principle in PRINCIPLES:
        p_issues = [i for i in issues if i.get("principle") == principle]
        penalty = sum(SEVERITY_PENALTY.get(i.get("severity", "minor"), 1) for i in p_issues)
        by_principle[principle] = {
            "score": max(0, 100 - penalty),
            "issues": len(p_issues),
        }

    by_severity = {
        sev: sum(1 for i in issues if i.get("severity") == sev)
        for sev in SEVERITY_PENALTY
    }

    return {
        "score": overall,
        "grade": _grade(overall),
        "total_issues": len(issues),
        "by_severity": by_severity,
        "by_principle": by_principle,
    }
