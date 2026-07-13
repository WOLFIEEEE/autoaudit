"""Tests for audit._wcag centralized principle mapping."""

from audit._wcag import DEFAULT_PRINCIPLE, principle_for


def test_first_digit_determines_principle():
    assert principle_for(["1.1.1"]) == "perceivable"
    assert principle_for(["1.4.3"]) == "perceivable"
    assert principle_for(["2.1.2"]) == "operable"
    assert principle_for(["2.4.4"]) == "operable"
    assert principle_for(["2.5.8"]) == "operable"
    assert principle_for(["3.1.1"]) == "understandable"
    assert principle_for(["3.3.2"]) == "understandable"
    assert principle_for(["4.1.2"]) == "robust"


def test_multiple_criteria_uses_first_recognized():
    # 3.3.2 and 4.1.2 on the same issue (forms-input-no-label).
    # First criterion wins.
    assert principle_for(["3.3.2", "4.1.2"]) == "understandable"


def test_empty_list_falls_back_to_default():
    assert principle_for([]) == DEFAULT_PRINCIPLE
    assert principle_for(None) == DEFAULT_PRINCIPLE


def test_unknown_criteria_falls_back_to_default():
    assert principle_for(["not-a-criterion", ""]) == DEFAULT_PRINCIPLE


def test_make_issue_derives_principle_from_wcag():
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t-1",
        module="x",
        rule="x-r",
        severity="minor",
        wcag=["1.4.3"],
        title="test",
    )
    assert issue["principle"] == "perceivable"


def test_make_issue_explicit_principle_overrides_derivation():
    """An explicit principle= kwarg is respected — escape hatch for rules
    whose semantic principle doesn't match the WCAG filing."""
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t-1",
        module="x",
        rule="x-r",
        severity="minor",
        wcag=["1.4.3"],
        title="test",
        principle="robust",
    )
    assert issue["principle"] == "robust"


def test_make_issue_attaches_fingerprint():
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t",
        module="x",
        rule="x-r",
        severity="minor",
        wcag=["1.4.3"],
        title="test",
        selector="#a",
        html_snippet="<a/>",
    )
    assert "fingerprint" in issue
    # Don't pin the exact length — the truncation width is an
    # implementation detail of issue_fingerprint. What we want to
    # guarantee is "non-empty, hex, deterministic".
    fp = issue["fingerprint"]
    assert isinstance(fp, str) and fp
    assert all(c in "0123456789abcdef" for c in fp)


def test_make_issue_attaches_evidence_list():
    """`evidence` starts with the detecting module so the dedup pass
    can merge evidence across overlapping findings."""
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t", module="keyboard", rule="x", severity="minor",
        wcag=["1.4.3"], title="",
    )
    assert issue["evidence"] == ["keyboard"]


def test_make_issue_attaches_understanding_url():
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t", module="x", rule="x", severity="minor",
        wcag=["1.4.3"], title="",
    )
    assert issue["understanding_url"] and "contrast-minimum" in issue["understanding_url"]


def test_make_issue_level_derived_from_wcag():
    """A level-AA SC should propagate to the issue's level field."""
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t", module="x", rule="x", severity="minor",
        wcag=["1.4.3"], title="",
    )
    assert issue["level"] == "AA"


def test_make_issue_level_none_for_obsolete_only_wcag():
    """Obsolete 4.1.1 alone → level None (doesn't block 2.2 conformance)."""
    from audit._issue import make_issue

    issue = make_issue(
        issue_id="t", module="x", rule="x", severity="minor",
        wcag=["4.1.1"], title="",
    )
    assert issue["level"] is None
