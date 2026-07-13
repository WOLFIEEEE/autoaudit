"""Merge duplicate issues within and across modules.

Two issues are treated as duplicates when they describe the same DOM
element and the same rule. Same-element is determined by the shared
fingerprint attached in audit._issue.make_issue (falls back to raw
selector when a caller's issue predates fingerprinting). Higher
severity wins; on ties, first seen wins.

We ALSO collapse known cross-module overlaps: axe-core's rule IDs
often describe the same failure as one of our custom rules (e.g.
axe's `aria-hidden-focus` and our `aria-hidden-focusable`). We keep
ours because the fix_suggestion is usually more specific and
actionable, but merge in axe's wcag_criteria / details so nothing is
lost from the axe run. The canonical pairing list lives in
CROSS_MODULE_ALIASES below and is kept explicit — a static table is
easier to audit than clever pattern matching.

Beyond raw collapsing, the dedup pass also fuses `evidence` sources so
a kept issue carries the union of every detector that flagged it. This
lets the report surface high-confidence findings (flagged by axe AND
our custom rule AND NVDA) distinctly from single-source findings.
"""

from __future__ import annotations

from typing import Any

from audit._fingerprint import (
    fingerprint_for_issue,
    issue_fingerprint,
    normalize_selector,
)

SEVERITY_RANK = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}

# Known same-issue-different-id pairings: axe rule → our rule.
# When both fire on the same element, collapse to our rule (because
# our fix_suggestion is usually more opinionated) and retain axe's
# wcag_criteria on a secondary "also_detected_by" field.
#
# CI-side invariant: every value here should be a rule id emitted by
# one of our custom modules. `validate_cross_module_aliases` (below)
# lets tests assert this so the table never drifts after a rename.
CROSS_MODULE_ALIASES: dict[str, str] = {
    # axe rule            → our rule
    "aria-hidden-focus":    "aria-hidden-focusable",
    "button-name":          "sr-silent-interactive",
    "image-alt":            "media-img-no-alt",
    "role-img-alt":         "media-img-no-alt",
    "label":                "forms-input-no-label",
    "target-size":          "responsive-target-size",
    "meta-viewport":        "responsive-viewport-zoom-disabled",
    "link-name":            "sr-silent-interactive",
    "heading-order":        "structure-heading-skip",
    "html-has-lang":        "structure-html-lang",
    "document-title":       "structure-title-missing",
    "aria-valid-attr":      "aria-invalid-role",
    "aria-allowed-role":    "aria-invalid-role",
    "duplicate-id-aria":    "aria-labelledby-missing",
    # Path A (a11y tree) → Path B (real NVDA) reconciliation. When
    # NVDA actually observed the failure, that's stronger evidence
    # than the markup-only inspection — so we drop the Path A variant
    # and fold its evidence into the Path B keeper. Path A rules
    # remain when NVDA isn't available (most deployments), so this
    # only collapses findings on a full Windows-worker run.
    "sr-silent-interactive": "sr-nvda-silent",
    "sr-label-in-name":      "sr-nvda-mismatch",
}


def _selector(issue: dict[str, Any]) -> str:
    return (issue.get("element") or {}).get("selector", "")


def _fp(issue: dict[str, Any]) -> str:
    """Return a stable rule-and-element key for final-pass dedup.

    Prefers the pre-attached `fingerprint` (emitted by make_issue).
    When absent — legacy issues constructed before fingerprinting or
    tests that pass raw dicts — compute it on the fly. Falls back to
    selector-only identity as a final safety net so the dedup still
    runs even on minimal test fixtures.

    Because the fingerprint embeds the rule id, two different rules
    on the same element yield different fp values — exactly what we
    want for final-pass dedup (don't collapse unrelated rules).
    """
    fp = issue.get("fingerprint")
    if fp:
        return fp
    if issue.get("rule"):
        return fingerprint_for_issue(issue)
    return _selector(issue)


def _element_key(issue: dict[str, Any]) -> str:
    """Element-only identity (NO rule component).

    Cross-module alias matching needs this: "did axe rule X and our
    rule Y fire on the same DOM element?" Two modules often synthesize
    DIFFERENT selector strings for the same DOM node (`#a > img` vs
    `img.logo`). Axe's exported html_snippet comes from the same
    outerHTML serialization we use, so when snippets match exactly
    we're almost always looking at the same element. Preferring the
    snippet hash over the normalized selector makes this case work.

    Falls back to the normalized selector when no snippet is available
    (page-level rules, test fixtures without snippets).
    """
    el = issue.get("element") or {}
    snip = el.get("html_snippet")
    if snip:
        return f"snip:{issue_fingerprint(rule='', html_snippet=snip)}"
    sel = normalize_selector(_selector(issue))
    if sel:
        return f"sel:{sel}"
    # Page-level rule, no element — treat every instance as its own
    # "element" (keyed by id()) so distinct page-level findings don't
    # wrongly share a key.
    return f"id:{id(issue)}"


def _key(issue: dict[str, Any]) -> str:
    # After cross-module aliasing has been applied, a duplicate is
    # defined as "same element (fingerprint), same rule id." We keep
    # the rule in the key so distinct rules on the same element stay
    # separate even when they share a fingerprint prefix.
    return f"{_fp(issue)}::{issue.get('rule', '')}"


def _merge_evidence(
    kept: dict[str, Any],
    dropped: dict[str, Any],
) -> None:
    """Union `evidence` sources from two duplicate issues into `kept`.

    Evidence is source-attribution: a finding flagged by both axe and
    our custom rule is higher-confidence than a finding flagged by
    either alone. We preserve the union so report consumers can show
    multi-source findings first.
    """
    existing = list(kept.get("evidence") or [kept.get("module", "")])
    incoming = list(dropped.get("evidence") or [dropped.get("module", "")])
    merged: list[str] = []
    for src in existing + incoming:
        if src and src not in merged:
            merged.append(src)
    kept["evidence"] = merged


def _apply_cross_module_aliases(
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """First pass: for every (element, axe-rule) that has a known
    custom-rule sibling firing on the same element, drop the axe
    variant but fold its wcag_criteria and evidence into the kept
    issue.

    Keeping both in the raw output and letting the second-pass
    dedup remove the duplicate wouldn't work — the two issues have
    different rule ids, so the second pass would never merge them.
    """
    # Index by element-only key (no rule) so we can probe each
    # element's rule set — robust to two modules having synthesized
    # different selector strings for the same DOM node, AND necessary
    # so "axe rule X and our rule Y on the same element" is detectable
    # (their per-rule fingerprints would differ).
    rules_by_element: dict[str, set[str]] = {}
    for i in issues:
        rules_by_element.setdefault(_element_key(i), set()).add(i.get("rule", ""))

    out: list[dict[str, Any]] = []
    # Track which (element_key, our_rule) combos should receive the
    # merged-in wcag metadata so we only tag the kept issue once.
    merged_meta: dict[tuple[str, str], dict[str, Any]] = {}

    for issue in issues:
        rule = issue.get("rule", "")
        ekey = _element_key(issue)
        canonical = CROSS_MODULE_ALIASES.get(rule)
        # Drop the alias variant only when our canonical equivalent
        # also fired on the same element. If ours didn't fire, the
        # alias finding is unique coverage and must be preserved.
        if canonical and canonical in rules_by_element.get(ekey, set()):
            bucket = merged_meta.setdefault(
                (ekey, canonical),
                {"criteria": set(), "evidence": []},
            )
            bucket["criteria"].update(issue.get("wcag_criteria") or [])
            for src in issue.get("evidence") or [issue.get("module", "")]:
                if src and src not in bucket["evidence"]:
                    bucket["evidence"].append(src)
            continue
        out.append(issue)

    # Second pass: enrich the kept custom rules with the axe-side
    # WCAG criteria and evidence we dropped. Consumers writing a VPAT
    # benefit from seeing every SC that reported the failure, and the
    # HTML report can show multi-source confidence.
    for kept in out:
        key = (_element_key(kept), kept.get("rule", ""))
        extra = merged_meta.get(key)
        if not extra:
            continue
        existing_criteria = set(kept.get("wcag_criteria") or [])
        added = sorted(extra["criteria"] - existing_criteria)
        if added:
            kept["wcag_criteria"] = sorted(existing_criteria | set(added))
            kept.setdefault("details", {}).setdefault(
                "also_detected_by", []
            ).extend(sorted(extra["criteria"]))
        # Always merge evidence (even when no new SCs were added — the
        # axe attribution itself is useful signal).
        existing_ev = list(kept.get("evidence") or [kept.get("module", "")])
        for src in extra["evidence"]:
            if src and src not in existing_ev:
                existing_ev.append(src)
        kept["evidence"] = existing_ev

    return out


def deduplicate_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues = _apply_cross_module_aliases(issues)

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
            # New one wins — merge the old one's evidence into it before
            # swapping so attribution is preserved.
            _merge_evidence(issue, existing)
            best_by_key[k] = issue
        else:
            _merge_evidence(existing, issue)

    return [best_by_key[k] for k in order]


# ---------------------------------------------------------------------
# CI-side invariant check. Tests call this to confirm the alias table
# still matches the rules the modules actually emit — prevents silent
# drift when a rule is renamed or retired.
# ---------------------------------------------------------------------


def validate_cross_module_aliases(known_custom_rules: set[str]) -> list[str]:
    """Return a list of alias-map errors relative to the given rule set.

    `known_custom_rules` is the union of rule IDs emitted by every
    non-axe module. An empty return value means the table is coherent.
    Items in the returned list are human-readable error strings suitable
    for direct use in an assertion message.
    """
    errors: list[str] = []
    for axe_rule, our_rule in CROSS_MODULE_ALIASES.items():
        if our_rule not in known_custom_rules:
            errors.append(
                f"CROSS_MODULE_ALIASES maps axe '{axe_rule}' → "
                f"'{our_rule}', but '{our_rule}' is not emitted by any "
                "current custom module. Rename or remove the entry."
            )
    return errors
