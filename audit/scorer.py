"""Score and grade an audit from its list of issues.

Three design choices worth flagging up front:

1. **Log-scaled instance penalty.** A linear penalty-per-issue punishes
   pages unfairly: one real contrast bug echoed across 40 table cells
   is one design defect, not forty. We count each (rule, fingerprint)
   combo once, then apply `penalty(sev) * (1 + log2(instance_count))`
   for that rule. Ten instances of a rule add ~4.3× a single instance,
   not 10×. Two instances add ~2× — still punished, but proportional.

2. **Confidence weighting.** Issues carry a `confidence` tier (see
   audit._issue.make_issue). Low-confidence heuristics get 0.5×,
   medium 0.8×, high 1.0×. Reported grades become resistant to false
   positives from suggestive rules; auditors still see the issues in
   the report, they just don't dominate the score.

3. **Weakest-principle headline.** A page that's perfect on Robust
   but catastrophic on Operable averages to "fine" under the overall
   score. We surface `weakest_principle` as a first-class summary
   field so stakeholders see where to invest first.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

SEVERITY_PENALTY: dict[str, int] = {
    "critical": 8,
    "serious": 4,
    "moderate": 2,
    "minor": 1,
}

PRINCIPLES = ("perceivable", "operable", "understandable", "robust")
LEVELS = ("A", "AA", "AAA")

# Confidence → score multiplier. Low-confidence visual/pattern
# heuristics shouldn't dominate a grade. Keep medium close to 1× so
# most issues still carry their full weight.
CONFIDENCE_MULTIPLIER: dict[str, float] = {
    "high": 1.0,
    "medium": 0.8,
    "low": 0.5,
}


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


def _group_key(issue: dict[str, Any], idx: int) -> tuple:
    """Return the key used to collapse instances of the same defect.

    Uses (rule, fingerprint) when both are present. When either is
    missing — legacy/test inputs — falls back to the issue's index
    so each such issue becomes its own group (no collapsing), which
    preserves pre-fingerprint scoring behavior for callers that haven't
    adopted make_issue yet.
    """
    rule = issue.get("rule")
    fp = issue.get("fingerprint")
    if rule and fp:
        return (rule, fp)
    if rule:
        return ("rule", rule, idx)
    return ("unkeyed", idx)


def _penalty_for_group(severity: str, confidence: str, count: int) -> float:
    """Compute the penalty contribution of a single rule+element group.

    `count` is the number of times the group appeared before dedup — a
    design-system bug echoed across a page. We apply log2 so the
    penalty grows but doesn't dominate. `confidence` scales the whole
    group; all instances of a rule share the rule's confidence tier
    (it's set at the rule level, not per instance).
    """
    base = SEVERITY_PENALTY.get(severity, 1)
    mult = CONFIDENCE_MULTIPLIER.get(confidence, 1.0)
    # log2(1) = 0, so a singleton contributes exactly `base * mult`.
    # log2(2) = 1 → doubles; log2(16) = 4 → 5× the single-instance cost.
    growth = 1.0 + math.log2(max(count, 1))
    return base * mult * growth


def _aggregate_penalty(issues: list[dict[str, Any]]) -> float:
    """Sum the log-scaled confidence-weighted penalty across all groups."""
    groups: dict[tuple, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "severity": "minor", "confidence": "high"}
    )
    for idx, issue in enumerate(issues):
        key = _group_key(issue, idx)
        g = groups[key]
        g["count"] += 1
        # Keep the worst severity seen in a group — safer for stakeholders.
        if SEVERITY_PENALTY.get(issue.get("severity", "minor"), 1) > SEVERITY_PENALTY.get(g["severity"], 0):
            g["severity"] = issue.get("severity", "minor")
        # Confidence: pessimistically keep the lowest. A rule flagged
        # once as "high" and once as "low" on the same element is a
        # contradiction we shouldn't paper over with the higher value.
        new_conf = issue.get("confidence", "high")
        if CONFIDENCE_MULTIPLIER.get(new_conf, 1.0) < CONFIDENCE_MULTIPLIER.get(g["confidence"], 1.0):
            g["confidence"] = new_conf

    return sum(
        _penalty_for_group(g["severity"], g["confidence"], g["count"])
        for g in groups.values()
    )


def calculate_scores(issues: list[dict[str, Any]]) -> dict[str, Any]:
    total_penalty = _aggregate_penalty(issues)
    overall = max(0, 100 - int(round(total_penalty)))

    by_principle: dict[str, dict[str, int]] = {}
    for principle in PRINCIPLES:
        p_issues = [i for i in issues if i.get("principle") == principle]
        penalty = _aggregate_penalty(p_issues)
        by_principle[principle] = {
            "score": max(0, 100 - int(round(penalty))),
            "issues": len(p_issues),
        }
    # Sum-consistency: issues with no recognized principle go into
    # `unmapped` so every issue appears exactly once across principle
    # buckets. Previously these contributed to overall but no principle.
    unmapped_issues = [
        i for i in issues if i.get("principle") not in PRINCIPLES
    ]
    if unmapped_issues:
        by_principle["unmapped"] = {
            "score": max(0, 100 - int(round(_aggregate_penalty(unmapped_issues)))),
            "issues": len(unmapped_issues),
        }

    # Weakest-principle headline: the lowest principle score (among
    # principles with at least one issue). A page can have overall=85
    # yet `perceivable=45` — the headline points at the real problem.
    scored_principles = {
        p: data["score"]
        for p, data in by_principle.items()
        if p in PRINCIPLES and data["issues"] > 0
    }
    weakest: str | None = (
        min(scored_principles, key=scored_principles.get)
        if scored_principles
        else None
    )

    by_severity = {
        sev: sum(1 for i in issues if i.get("severity") == sev)
        for sev in SEVERITY_PENALTY
    }
    by_confidence = {
        tier: sum(1 for i in issues if i.get("confidence") == tier)
        for tier in CONFIDENCE_MULTIPLIER
    }

    # WCAG conformance-level rollup. Each bucket is the count of
    # issues whose strictest mapped SC falls at that level. An issue
    # with no recognized SC (e.g. a custom performance rule, or one
    # citing only the obsolete 4.1.1) lands in "unmapped" so the
    # totals stay sum-consistent — every issue appears exactly once
    # across the level buckets.
    by_level: dict[str, dict[str, Any]] = {}
    for lvl in LEVELS:
        l_issues = [i for i in issues if i.get("level") == lvl]
        by_level[lvl] = {
            "issues": len(l_issues),
            # A report consumer wants to know "what's blocking AA?"
            # — that's issues at level A or AA (both block AA claims).
            "blocks_conformance_at_this_level_or_above": True,
            # Per-severity fan-out so a VPAT-style summary can render
            # "AA: 3 critical / 2 serious / …" without re-filtering.
            "by_severity": {
                sev: sum(1 for i in l_issues if i.get("severity") == sev)
                for sev in SEVERITY_PENALTY
            },
        }
    by_level["unmapped"] = {
        "issues": sum(1 for i in issues if not i.get("level")),
    }

    # An automated scan can only report whether its rule set found a
    # blocker; it cannot establish WCAG conformance. Conformance requires
    # every applicable success criterion and full pages/processes to pass,
    # including criteria that require manual review. Keep the rollup useful
    # without emitting an overbroad `*_conformant` boolean.
    no_detected_failures = {
        "A": by_level["A"]["issues"] == 0,
        "AA": by_level["A"]["issues"] == 0 and by_level["AA"]["issues"] == 0,
        "AAA": all(by_level[level]["issues"] == 0 for level in LEVELS),
    }

    # Distinct-defect count: how many unique (rule, element) groups
    # fired. Often more useful than raw issue count for dashboards
    # ("23 defects, repeated 180× across the page").
    distinct_defects = len({
        _group_key(i, idx) for idx, i in enumerate(issues)
    })

    return {
        "score": overall,
        "grade": _grade(overall),
        "total_issues": len(issues),
        "distinct_defects": distinct_defects,
        "weakest_principle": weakest,
        "by_severity": by_severity,
        "by_confidence": by_confidence,
        "by_principle": by_principle,
        "by_level": by_level,
        "conformance": {
            "status": "not_determined",
            "manual_review_required": True,
            "no_detected_failures": no_detected_failures,
        },
    }
