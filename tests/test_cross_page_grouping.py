"""Tests for the cross-page grouping helper in orchestrator.

`_group_across_pages` is a pure function operating on the aggregated
issues list (each with page_url tagged). These tests exercise it in
isolation — no browser, no Celery.
"""

from audit.orchestrator import _group_across_pages
from audit._issue import make_issue


def _issue(rule, page, selector="#x", snippet="<x/>", severity="serious"):
    i = make_issue(
        issue_id=f"{rule}-{page}",
        module="m",
        rule=rule,
        severity=severity,
        wcag=["1.4.3"],
        title="",
        selector=selector,
        html_snippet=snippet,
    )
    i["page_url"] = page
    return i


def test_same_rule_same_element_across_pages_groups_into_one():
    issues = [
        _issue("r-a", "https://site/a"),
        _issue("r-a", "https://site/b"),
        _issue("r-a", "https://site/c"),
    ]
    groups = _group_across_pages(issues)
    assert len(groups) == 1
    g = groups[0]
    assert g["rule"] == "r-a"
    assert g["instance_count"] == 3
    assert set(g["pages_affected"]) == {
        "https://site/a", "https://site/b", "https://site/c",
    }


def test_different_elements_stay_distinct():
    issues = [
        _issue("r-a", "https://s/1", selector="#x", snippet="<x/>"),
        _issue("r-a", "https://s/1", selector="#y", snippet="<y/>"),
    ]
    groups = _group_across_pages(issues)
    assert len(groups) == 2


def test_ordering_prioritizes_severity_then_pages_affected():
    """Sort order: worst severity first, then widest spread first."""
    issues = [
        _issue("moderate-bug", "https://s/a", severity="moderate"),
        _issue("moderate-bug", "https://s/b", severity="moderate"),
        _issue("moderate-bug", "https://s/c", severity="moderate"),
        _issue("critical-bug", "https://s/a", selector="#y", snippet="<y/>",
               severity="critical"),
    ]
    groups = _group_across_pages(issues)
    # Critical wins on severity even though it's on fewer pages.
    assert groups[0]["rule"] == "critical-bug"
    assert groups[1]["rule"] == "moderate-bug"
    assert len(groups[1]["pages_affected"]) == 3


def test_empty_input_returns_empty_list():
    assert _group_across_pages([]) == []


def test_page_level_rules_distinct_across_pages():
    """A title-missing finding on two different pages must remain two
    separate groups — the fingerprint helper folds page_url into the
    basis when selector+snippet are empty."""
    a = make_issue(
        issue_id="t-a", module="structure", rule="structure-title-missing",
        severity="moderate", wcag=["2.4.2"], title="",
    )
    a["page_url"] = "https://site/a"
    # Recompute fingerprint with page_url.
    from audit._fingerprint import fingerprint_for_issue
    a["fingerprint"] = fingerprint_for_issue(a)

    b = make_issue(
        issue_id="t-b", module="structure", rule="structure-title-missing",
        severity="moderate", wcag=["2.4.2"], title="",
    )
    b["page_url"] = "https://site/b"
    b["fingerprint"] = fingerprint_for_issue(b)

    groups = _group_across_pages([a, b])
    assert len(groups) == 2
