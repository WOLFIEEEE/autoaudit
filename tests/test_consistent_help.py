"""Unit tests for audit/consistent_help.py (WCAG 3.2.6).

Two tiers:
  - `_classify` — the keyword/href-based mapping into help kinds.
  - `analyze_cross_page` — the cross-page order comparison.

We don't exercise the JS probe here; tests/integration/test_e2e.py
covers it end-to-end against a live page. The Python logic is what
governs whether 3.2.6 issues fire and is the failure mode most
likely to regress.
"""

from __future__ import annotations

import pytest

from audit import consistent_help


# -- classify ----------------------------------------------------------


@pytest.mark.parametrize(
    "name,href,expected",
    [
        ("Contact us", "", "contact"),
        ("Talk to a human", "", "contact"),
        ("FAQ", "", "self_help"),
        ("Help center", "", "self_help"),
        ("Live chat", "", "chat"),
        ("Start chat", "", "chat"),
        ("", "tel:+1-555-0100", "phone"),
        ("Call sales", "tel:+1-555-0100", "phone"),
        ("", "mailto:hello@example.com", "email"),
        # Path-based fallback when the accessible name is non-textual.
        ("→", "/contact-us", "contact"),
        ("?", "/help/articles/123", "self_help"),
        # Should NOT classify generic links.
        ("Home", "/", None),
        ("Pricing", "/pricing", None),
        ("Login", "/account/login", None),
        # Empty input.
        ("", "", None),
    ],
)
def test_classify(name: str, href: str, expected: str | None):
    assert consistent_help._classify(name, href) == expected


# -- analyze_cross_page ------------------------------------------------


def _result(*mechs):
    """Build a fake collect() return value from (kind, order) tuples."""
    mechanisms = [
        {"kind": k, "order": o, "landmark": "footer", "accessible_name": k,
         "selector": f"#{k}", "href": "", "outer_html": ""}
        for k, o in mechs
    ]
    return {"ran": True, "mechanisms": mechanisms, "duration_seconds": 0.0}


def test_single_page_audit_emits_no_issues():
    """3.2.6 is fundamentally a multi-page concern — one URL → no findings."""
    issues = consistent_help.analyze_cross_page([
        ("https://x", _result(("contact", 1), ("chat", 2))),
    ])
    assert issues == []


def test_consistent_order_emits_nothing():
    issues = consistent_help.analyze_cross_page([
        ("https://x/a", _result(("contact", 5), ("chat", 12), ("self_help", 30))),
        ("https://x/b", _result(("contact", 4), ("chat", 9),  ("self_help", 41))),
    ])
    assert issues == []


def test_inverted_order_flags_a_finding():
    issues = consistent_help.analyze_cross_page([
        ("https://x/a", _result(("contact", 5),  ("chat", 12))),
        # On page b, chat (order 1) precedes contact (order 8): inverted.
        ("https://x/b", _result(("chat", 1),     ("contact", 8))),
    ])
    assert len(issues) == 1
    issue = issues[0]
    assert issue["rule"] == "consistent-help-relative-order-changed"
    assert "3.2.6" in issue["wcag_criteria"]
    # Severity is moderate per the module's policy.
    assert issue["severity"] == "moderate"


def test_only_one_finding_per_page_even_with_multiple_inversions():
    """Multiple pairwise inversions on the same page collapse to one issue."""
    issues = consistent_help.analyze_cross_page([
        ("https://x/a", _result(
            ("contact", 1), ("chat", 2), ("self_help", 3), ("phone", 4),
        )),
        # Fully reversed.
        ("https://x/b", _result(
            ("contact", 4), ("chat", 3), ("self_help", 2), ("phone", 1),
        )),
    ])
    assert len(issues) == 1


def test_disjoint_mechanisms_dont_constrain_each_other():
    """Page-A has only 'contact'; Page-B has only 'chat'. No shared
    set means no order to violate."""
    issues = consistent_help.analyze_cross_page([
        ("https://x/a", _result(("contact", 1))),
        ("https://x/b", _result(("chat", 1))),
    ])
    assert issues == []


def test_skipped_collector_results_are_ignored():
    issues = consistent_help.analyze_cross_page([
        ("https://x/a", _result(("contact", 1), ("chat", 2))),
        ("https://x/b", {"ran": False, "error": "boom", "mechanisms": []}),
        ("https://x/c", _result(("chat", 1), ("contact", 5))),  # inverted vs A
    ])
    # Only c's inversion should fire (b skipped, a is baseline).
    assert len(issues) == 1
    assert "x/c" in issues[0]["title"]
