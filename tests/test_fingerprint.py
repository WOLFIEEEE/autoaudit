"""Tests for audit/_fingerprint.py — stable issue fingerprints.

These cover the two guarantees we depend on downstream:
- Deterministic across runs (same inputs → same hash).
- Resilient to the cosmetic selector/snippet noise that shifts between
  runs without any real content change (nth-of-type drift, CSS-in-JS
  hashes, whitespace).
"""

from audit._fingerprint import (
    fingerprint_for_issue,
    issue_fingerprint,
    normalize_selector,
)


def test_fingerprint_is_deterministic():
    a = issue_fingerprint(
        rule="color-contrast",
        selector="#banner .cta",
        html_snippet="<button class='cta'>Go</button>",
        wcag_criteria=["1.4.3"],
    )
    b = issue_fingerprint(
        rule="color-contrast",
        selector="#banner .cta",
        html_snippet="<button class='cta'>Go</button>",
        wcag_criteria=["1.4.3"],
    )
    assert a == b
    # 16 hex chars — the shape downstream code assumes.
    assert len(a) == 16


def test_fingerprint_is_stable_across_nth_of_type_drift():
    """A new sibling shifts nth-of-type indexes. Fingerprint must not
    change — that's the whole point of normalizing positional noise."""
    with_nth = issue_fingerprint(
        rule="r",
        selector="main > ul > li:nth-of-type(3) > a",
        html_snippet="<a>link</a>",
    )
    without_nth = issue_fingerprint(
        rule="r",
        selector="main > ul > li:nth-of-type(7) > a",
        html_snippet="<a>link</a>",
    )
    assert with_nth == without_nth


def test_fingerprint_strips_css_in_js_hash_ids():
    a = issue_fingerprint(
        rule="r",
        selector="#css-1q0lrm2 > button",
        html_snippet="<button>x</button>",
    )
    b = issue_fingerprint(
        rule="r",
        selector="#css-abc999z > button",
        html_snippet="<button>x</button>",
    )
    assert a == b


def test_fingerprint_distinguishes_different_elements():
    a = issue_fingerprint(rule="r", selector="#a", html_snippet="<a/>")
    b = issue_fingerprint(rule="r", selector="#b", html_snippet="<b/>")
    assert a != b


def test_fingerprint_distinguishes_different_rules():
    a = issue_fingerprint(rule="r1", selector="#x", html_snippet="<x/>")
    b = issue_fingerprint(rule="r2", selector="#x", html_snippet="<x/>")
    assert a != b


def test_fingerprint_distinguishes_different_wcag():
    """Same element, same rule, different WCAG tuple — these genuinely
    differ in reporting (e.g. a rule double-mapped)."""
    a = issue_fingerprint(rule="r", selector="#x", html_snippet="<x/>", wcag_criteria=["1.4.3"])
    b = issue_fingerprint(rule="r", selector="#x", html_snippet="<x/>", wcag_criteria=["2.4.7"])
    assert a != b


def test_page_level_fingerprint_uses_page_url():
    """No selector + no snippet → same rule on two pages must produce
    two distinct fingerprints (otherwise cross-page grouping would
    wrongly collapse them)."""
    a = issue_fingerprint(
        rule="structure-title-missing",
        selector="",
        html_snippet="",
        page_url="https://a.example/",
    )
    b = issue_fingerprint(
        rule="structure-title-missing",
        selector="",
        html_snippet="",
        page_url="https://b.example/",
    )
    assert a != b


def test_fingerprint_for_issue_reads_dict_fields():
    issue = {
        "rule": "color-contrast",
        "wcag_criteria": ["1.4.3"],
        "element": {
            "selector": "#x",
            "html_snippet": "<x/>",
        },
    }
    direct = issue_fingerprint(
        rule="color-contrast",
        selector="#x",
        html_snippet="<x/>",
        wcag_criteria=["1.4.3"],
    )
    assert fingerprint_for_issue(issue) == direct


def test_normalize_selector_strips_positional_noise():
    assert normalize_selector("main > ul > li:nth-of-type(3) > a") == "main > ul > li > a"
    assert normalize_selector("  button.cta  ") == "button.cta"
    assert normalize_selector(None) == ""
    assert normalize_selector("") == ""
