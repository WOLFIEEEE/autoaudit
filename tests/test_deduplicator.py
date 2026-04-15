from audit.deduplicator import deduplicate_issues


def test_empty_input():
    assert deduplicate_issues([]) == []


def test_unique_issues_passthrough():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#a"}},
        {"rule": "label-missing", "severity": "critical", "element": {"selector": "#b"}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_same_element_same_rule_family_deduped():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#x"}},
        {"rule": "color-ratio", "severity": "critical", "element": {"selector": "#x"}},
    ]
    deduped = deduplicate_issues(issues)
    assert len(deduped) == 1
    # Higher severity (critical) wins.
    assert deduped[0]["severity"] == "critical"


def test_different_selectors_not_merged():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#x"}},
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#y"}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_preserves_first_seen_order():
    issues = [
        {"rule": "r-one", "severity": "minor", "element": {"selector": "#a"}},
        {"rule": "s-one", "severity": "minor", "element": {"selector": "#b"}},
        {"rule": "r-two", "severity": "minor", "element": {"selector": "#a"}},  # dup of first
    ]
    deduped = deduplicate_issues(issues)
    assert [i["element"]["selector"] for i in deduped] == ["#a", "#b"]
