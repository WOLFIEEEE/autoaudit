"""Unit tests for audit/wcag_coverage.py.

The registry is hand-edited — tests guard against typos and
silent-drift more than against logic bugs.
"""

from __future__ import annotations

import pytest

from audit import wcag_coverage


def test_every_entry_has_required_fields():
    required = {"level", "name", "tier", "notes"}
    for sc, entry in wcag_coverage.COVERAGE.items():
        missing = required - entry.keys()
        assert not missing, f"{sc}: missing fields {missing}"
        assert entry["level"] in {"A", "AA", "AAA"}, f"{sc}: bad level {entry['level']!r}"
        assert entry["tier"] in {"automated", "ai_assisted", "partial", "manual_only"}, (
            f"{sc}: bad tier {entry['tier']!r}"
        )
        # Notes must be substantial — empty strings would be a sign
        # someone added an SC without explaining why it's at this tier.
        assert len(entry["notes"]) >= 20, f"{sc}: notes too short to be useful"


def test_wcag_2_2_new_criteria_present():
    """The five SCs added in WCAG 2.2 should all appear in the registry."""
    new_in_22 = {"2.4.11", "2.5.7", "2.5.8", "3.2.6", "3.3.7", "3.3.8"}
    missing = new_in_22 - set(wcag_coverage.COVERAGE)
    assert not missing, f"WCAG 2.2 SCs missing from registry: {missing}"


def test_2_5_8_is_automated():
    """Sanity: 2.5.8 must be automated now that target_size.py exists."""
    assert wcag_coverage.COVERAGE["2.5.8"]["tier"] == "automated"


def test_3_2_6_is_automated():
    """Sanity: 3.2.6 must be automated now that consistent_help.py exists."""
    assert wcag_coverage.COVERAGE["3.2.6"]["tier"] == "automated"


def test_2_5_7_3_3_7_3_3_8_have_partial_or_better_coverage():
    """Originally manual_only; the latest enhancement pass added
    heuristic detection for each. Lock the registry against silent
    regression to manual_only without coordinated rule deletion."""
    for sc in ("2.5.7", "3.3.7", "3.3.8"):
        tier = wcag_coverage.COVERAGE[sc]["tier"]
        assert tier in ("partial", "automated"), (
            f"{sc}: tier dropped back to {tier!r}; rules in "
            f"audit/{sc.replace('.', '_')}* should still be wired up"
        )


@pytest.mark.parametrize("level,expected_includes,expected_excludes", [
    ("A",   {"2.5.3", "3.2.6"},          {"1.4.3", "2.4.11"}),  # AA excluded
    ("AA",  {"1.4.3", "2.4.11", "2.5.8"}, set()),              # all in scope
    ("AAA", {"1.4.3", "2.5.8"},          set()),                # AA still in
])
def test_report_filters_by_level(level: str, expected_includes: set, expected_excludes: set):
    rep = wcag_coverage.report(target_level=level)
    seen = {
        sc["sc"]
        for bucket in ("automated", "ai_assisted", "partial", "manual_only")
        for sc in rep[bucket]
    }
    for sc in expected_includes:
        assert sc in seen, f"expected {sc} at level {level}"
    for sc in expected_excludes:
        assert sc not in seen, f"did NOT expect {sc} at level {level}"


def test_report_totals_consistent():
    rep = wcag_coverage.report(target_level="AA")
    totals = rep["totals"]
    assert (
        totals["automated"] + totals["ai_assisted"]
        + totals["partial"] + totals["manual_only"]
        == totals["in_scope"]
    )


def test_covered_total_is_automated_plus_ai_assisted():
    rep = wcag_coverage.report(target_level="AA")
    totals = rep["totals"]
    assert totals["covered"] == totals["automated"] + totals["ai_assisted"]
    # Blended figure must sit between "covered only" and "everything in
    # scope" — a sanity bound, not an exact target we want to hard-code.
    floor = round(100 * totals["covered"] / totals["in_scope"])
    assert floor <= totals["covered_pct"] <= 100


def test_ai_assisted_tier_populated():
    """The VLM-judged criteria must be at the ai_assisted tier — the
    whole point of keeping it distinct from automated. Locks against a
    silent regression that would re-hide the AI judgement as either
    'automated' (over-claiming) or 'partial' (under-claiming)."""
    for sc in ("1.1.1", "2.4.4", "2.4.6", "3.3.3"):
        assert wcag_coverage.COVERAGE[sc]["tier"] == "ai_assisted", (
            f"{sc} should be ai_assisted (covered by an audit/vlm.py rule)"
        )


def test_2_1_4_and_2_2_1_no_longer_manual_only():
    """char_key_shortcuts (2.1.4) and timing (2.2.1) modules now ship
    deterministic detection — these must not regress to manual_only."""
    for sc in ("2.1.4", "2.2.1"):
        assert wcag_coverage.COVERAGE[sc]["tier"] in ("partial", "automated"), (
            f"{sc} dropped back to manual_only; its module should be wired up"
        )


def test_report_within_tier_sorted_by_sc_number():
    rep = wcag_coverage.report(target_level="AA")
    for tier in ("automated", "ai_assisted", "partial", "manual_only"):
        scs = [s["sc"] for s in rep[tier]]
        as_tuples = [tuple(int(p) for p in s.split(".") if p.isdigit()) for s in scs]
        assert as_tuples == sorted(as_tuples), f"{tier} not sorted: {scs}"
