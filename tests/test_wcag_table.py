"""Drift-prevention tests for the WCAG 2.2 success-criterion table in
audit/_wcag.py.

These tests exist to fail LOUDLY when the hand-transcribed level table
is edited carelessly — wrong level (A/AA/AAA mix-up), missed 2.2
addition, accidental reinstatement of obsolete 4.1.1, or input
normalization regression. Levels and counts are public facts from the
W3C Recommendation; assertions here encode them.
"""

from collections import Counter

from audit._wcag import (
    _OBSOLETE,
    _PRINCIPLE_BY_DIGIT,
    _WCAG_LEVELS,
    DEFAULT_PRINCIPLE,
    blocking_level_for,
    highest_level_present,
    is_obsolete,
    level_for,
    principle_for,
    understanding_url,
)


def test_level_values_are_all_valid():
    assert set(_WCAG_LEVELS.values()) <= {"A", "AA", "AAA", _OBSOLETE}


def test_wcag22_level_counts():
    """WCAG 2.2 Recommendation totals (excluding obsolete 4.1.1):
        A = 31 : 30 carried from 2.1 minus 4.1.1 plus 3.2.6 and 3.3.7
        AA = 24: 20 carried from 2.1 plus 2.4.11, 2.5.7, 2.5.8, 3.3.8
        AAA = 31: 28 carried from 2.1 plus 2.4.12, 2.4.13, 3.3.9
    Total enforceable = 86.
    A drift here usually means a hand-edit slipped a level digit."""
    counts = Counter(v for v in _WCAG_LEVELS.values() if v != _OBSOLETE)
    assert counts["A"] == 31, counts
    assert counts["AA"] == 24, counts
    assert counts["AAA"] == 31, counts


def test_every_sc_is_one_of_four_principles():
    for sc in _WCAG_LEVELS:
        assert sc[0] in _PRINCIPLE_BY_DIGIT, f"unexpected principle digit: {sc}"
        assert principle_for([sc]) == _PRINCIPLE_BY_DIGIT[sc[0]]


def test_blocking_level_prefers_A_over_AA_over_AAA():
    assert blocking_level_for(["1.4.3", "1.1.1"]) == "A"    # AA + A -> A
    assert blocking_level_for(["1.4.3", "1.4.6"]) == "AA"   # AA + AAA -> AA
    assert blocking_level_for(["1.4.6"]) == "AAA"


def test_highest_level_is_the_opposite_direction():
    """For informational tagging ('this rule touches AAA'), highest wins."""
    assert highest_level_present(["1.4.3", "1.1.1"]) == "AA"
    assert highest_level_present(["1.4.3", "1.4.6"]) == "AAA"


def test_obsolete_411_is_excluded_from_level_math():
    assert is_obsolete("4.1.1")
    assert not is_obsolete("4.1.2")
    # Obsolete alone -> no blocking level
    assert blocking_level_for(["4.1.1"]) is None
    # Obsolete mixed with real SCs -> real one wins
    assert blocking_level_for(["4.1.1", "4.1.2"]) == "A"


def test_level_for_backcompat_alias_still_works():
    """The old `level_for` name is an alias for `blocking_level_for`.
    Keep the alias intact for one deprecation cycle."""
    assert level_for is blocking_level_for
    assert level_for(["1.4.3"]) == "AA"


def test_input_normalization_accepts_messy_sc_strings():
    """Real-world SC references arrive as 'WCAG 1.4.3', '1.4.3 (AA)',
    'sc1.4.3', '1.4.03' — all should resolve to the same canonical id."""
    for raw in ("WCAG 1.4.3", "1.4.3 (AA)", "sc1.4.3", "1.4.03", "  1.4.3"):
        assert blocking_level_for([raw]) == "AA", raw
        assert principle_for([raw]) == "perceivable", raw


def test_unknown_and_empty_return_none():
    assert blocking_level_for([]) is None
    assert blocking_level_for(["not-a-criterion"]) is None
    assert blocking_level_for(["axe-internal-rule"]) is None


def test_empty_falls_back_to_robust_principle():
    assert principle_for([]) == DEFAULT_PRINCIPLE
    assert principle_for(None) == DEFAULT_PRINCIPLE
    assert principle_for(["unknown"]) == DEFAULT_PRINCIPLE


def test_new_22_criteria_present():
    for sc in ("2.4.11", "2.4.12", "2.4.13",
               "2.5.7", "2.5.8",
               "3.2.6", "3.3.7", "3.3.8", "3.3.9"):
        assert sc in _WCAG_LEVELS


def test_understanding_url_resolvable_for_real_sc():
    url = understanding_url("1.4.3")
    assert url and url.startswith("https://www.w3.org/TR/WCAG22/#")
    assert "contrast-minimum" in url


def test_understanding_url_none_for_unknown_or_invalid():
    assert understanding_url(None) is None
    assert understanding_url("not-a-sc") is None
    assert understanding_url("") is None


def test_understanding_url_handles_obsolete():
    """4.1.1 still has a resolvable URL — it's obsolete for level math,
    not hidden from citation."""
    url = understanding_url("4.1.1")
    assert url and "parsing" in url
