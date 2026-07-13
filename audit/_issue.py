"""Helpers for building issue dicts in the shape the orchestrator expects.

Modules call `make_issue(...)` instead of hand-rolling dicts so that fields
like `wcag_criteria`, `element`, and `details` stay consistently typed.

`principle` is optional: when omitted, it's derived from the WCAG criteria
via audit._wcag.principle_for. Modules should not pass principle explicitly
unless they have a specific reason to override the derived value.

Every emitted issue carries a `fingerprint` (stable across runs, used by
the deduplicator and the run-to-run diff) plus an `understanding_url`
pulled from the WCAG table. Both are derived — callers don't set them.
"""

from __future__ import annotations

from typing import Any

from audit._fingerprint import issue_fingerprint
from audit._wcag import blocking_level_for, principle_for, understanding_url
from audit.rule_versions import version_for


def make_issue(
    *,
    issue_id: str,
    module: str,
    rule: str,
    severity: str,
    wcag: list[str],
    title: str,
    principle: str | None = None,
    level: str | None = None,
    confidence: str = "high",
    description: str = "",
    selector: str = "",
    html_snippet: str = "",
    text: str = "",
    details: dict[str, Any] | None = None,
    fix: str = "",
) -> dict[str, Any]:
    fingerprint = issue_fingerprint(
        rule=rule,
        selector=selector,
        html_snippet=html_snippet,
        wcag_criteria=wcag,
    )

    # Pull the W3C "Understanding" URL for the primary SC. Auditors
    # building a VPAT cite these; exposing them on every issue costs
    # nothing and raises report credibility.
    primary_sc = next((c for c in (wcag or ()) if c and c.strip()), None)
    u_url = understanding_url(primary_sc) if primary_sc else None

    return {
        "id": issue_id,
        # A stable, rule-and-element-keyed hash. Distinct from `id`,
        # which modules still set to their own scheme for readability
        # in raw JSON. Dedup and audit-diff both key on `fingerprint`.
        "fingerprint": fingerprint,
        "module": module,
        "rule": rule,
        # Stable rule identity across runs. The version is bumped in
        # audit/rule_versions.py whenever the rule's logic changes —
        # a downstream auditor looking at last quarter's VPAT can
        # verify the rule's behaviour at the time the report was
        # generated. "0.0.0" indicates an unregistered rule (the test
        # in tests/test_rule_versions.py fails CI when this happens).
        "rule_version": version_for(rule),
        "severity": severity,
        "principle": principle if principle is not None else principle_for(wcag),
        # WCAG conformance level ("A", "AA", or "AAA"). Derived from the
        # SC list via _wcag.blocking_level_for, which picks the strictest
        # level referenced so the compliance story stays the most
        # pessimistic of the possibilities. Remains None when no SC is
        # recognized — honest "we don't know" rather than fabricating a
        # level. Obsolete SCs (4.1.1 in WCAG 2.2) are excluded.
        "level": level if level is not None else blocking_level_for(wcag),
        # Confidence tiers callers can set per rule:
        #   "high"       — deterministic (attribute present/absent, DOM
        #                  relationship). Expected FPR < 1%.
        #   "medium"     — heuristic with narrow false-positive surface
        #                  (e.g. text-overlap thresholds).
        #   "low"        — suggestive, needs manual confirmation. Used by
        #                  visual/pattern heuristics that can't prove the
        #                  finding by markup alone.
        # The scorer weights low-confidence issues at 0.5× and medium at
        # 0.8× — see audit/scorer.py. Defaults to "high" so existing
        # rules behave unchanged. Authors lower this when a rule is
        # known to have gray-area cases so auditors can sort by
        # confidence before chasing findings.
        "confidence": confidence,
        "wcag_criteria": wcag,
        "understanding_url": u_url,
        "title": title,
        "description": description,
        # `evidence` tracks which detection sources flagged the finding.
        # Most issues start with [module]; the dedup pass and Path A/B
        # reconciliation layer additional sources when they merge
        # overlapping findings. A single issue with evidence=["axe",
        # "screen_reader", "nvda"] is far higher-confidence than any
        # single-source equivalent, and the HTML report surfaces this.
        "evidence": [module],
        "element": {
            "selector": selector,
            "html_snippet": html_snippet,
            "text_content": text,
        },
        "details": details or {},
        "fix_suggestion": fix,
    }
