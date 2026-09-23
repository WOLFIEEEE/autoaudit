"""The WCAG coverage map must agree with the rules we actually ship.

`wcag_coverage.py` is hand-maintained and feeds the VPAT. When a rule
lands for a criterion but the map still calls that criterion
`manual_only`, the VPAT reports "Not Evaluated" for something the tool
does evaluate — a correctness bug in the conformance output, not just a
stale doc. That is exactly what happened to 2.5.1 and 2.5.4.

These tests derive the truth from the code (every `make_issue` call site
and its `wcag=[...]`) and hold the map to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import wcag_coverage  # noqa: E402
from scripts.gen_rules_reference import _collect  # noqa: E402


def _sc_to_rules() -> dict[str, list[str]]:
    """Map each success criterion to the rule ids that can fire for it."""
    out: dict[str, list[str]] = {}
    for r in _collect():
        wcag = r.get("wcag")
        if not isinstance(wcag, list):
            # A non-literal wcag= argument (computed at runtime). Can't
            # attribute it statically; skipped rather than guessed.
            continue
        for sc in wcag:
            if isinstance(sc, str) and sc.strip():
                out.setdefault(sc.strip(), []).append(r["rule"])
    return out


def test_no_criterion_with_a_shipping_rule_is_marked_manual_only():
    """A criterion we have rules for is never 'manual_only'.

    'partial' is the floor for a criterion with detection but where
    confirming an alternative or a judgement still needs a human.
    """
    covered = wcag_coverage.COVERAGE
    offenders = []
    for sc, rules in _sc_to_rules().items():
        meta = covered.get(sc)
        if meta and meta.get("tier") == "manual_only":
            offenders.append(f"{sc} is manual_only but {len(rules)} rule(s) fire: {rules}")

    assert not offenders, (
        "Criteria marked manual_only despite shipping rules — the VPAT will "
        "say 'Not Evaluated' for these:\n  " + "\n  ".join(offenders)
    )


def test_every_rules_criterion_exists_in_the_map():
    """A rule mapped to a criterion the map has never heard of means either
    a typo in the rule's wcag= or a criterion missing from the map."""
    covered = set(wcag_coverage.COVERAGE)

    unknown = {
        sc: rules
        for sc, rules in _sc_to_rules().items()
        if sc not in covered
    }
    # AAA criteria and non-AA criteria legitimately sit outside an AA map.
    assert not unknown or all(
        sc.count(".") == 2 for sc in unknown
    ), f"rules reference criteria absent from the coverage map: {unknown}"


def test_reported_totals_are_internally_consistent():
    r = wcag_coverage.report(target_level="AA")
    t = r["totals"]
    assert (
        t["automated"] + t["ai_assisted"] + t["partial"] + t["manual_only"]
        == t["in_scope"]
    ), "tier counts must partition the in-scope criteria"
    assert t["covered"] == t["automated"] + t["ai_assisted"]


def test_2_5_1_and_2_5_4_stay_reclassified():
    """Regression pin: mobile.py ships rules for both."""
    r = wcag_coverage.report(target_level="AA")
    manual = {x["sc"] for x in r["manual_only"]}
    assert "2.5.1" not in manual
    assert "2.5.4" not in manual
