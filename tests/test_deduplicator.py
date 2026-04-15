from audit.deduplicator import deduplicate_issues


def test_empty_input():
    assert deduplicate_issues([]) == []


def test_unique_issues_passthrough():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#a"}},
        {"rule": "label-missing", "severity": "critical", "element": {"selector": "#b"}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_same_element_same_rule_deduped_higher_severity_wins():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#x"}},
        {"rule": "color-contrast", "severity": "critical", "element": {"selector": "#x"}},
    ]
    deduped = deduplicate_issues(issues)
    assert len(deduped) == 1
    assert deduped[0]["severity"] == "critical"


def test_different_rules_on_same_element_not_merged():
    """Distinct rules on the same element stay separate — they describe
    different problems even if they share a prefix."""
    issues = [
        {"rule": "forms-input-no-label", "severity": "critical", "element": {"selector": "#x"}},
        {
            "rule": "forms-aria-invalid-no-description",
            "severity": "moderate",
            "element": {"selector": "#x"},
        },
    ]
    deduped = deduplicate_issues(issues)
    assert len(deduped) == 2


def test_different_selectors_not_merged():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#x"}},
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#y"}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_page_level_rules_with_empty_selector_stay_separate():
    issues = [
        {"rule": "structure-no-h1", "severity": "moderate", "element": {"selector": ""}},
        {"rule": "structure-no-main", "severity": "moderate", "element": {"selector": ""}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_preserves_first_seen_order():
    issues = [
        {"rule": "r-one", "severity": "minor", "element": {"selector": "#a"}},
        {"rule": "s-one", "severity": "minor", "element": {"selector": "#b"}},
        {"rule": "r-one", "severity": "minor", "element": {"selector": "#a"}},  # dup of first
    ]
    deduped = deduplicate_issues(issues)
    assert [i["element"]["selector"] for i in deduped] == ["#a", "#b"]
