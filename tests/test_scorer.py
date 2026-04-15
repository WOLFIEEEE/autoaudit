from audit.scorer import calculate_scores


def test_empty_issues_gives_perfect_score():
    summary = calculate_scores([])
    assert summary["score"] == 100
    assert summary["grade"] == "A"
    assert summary["total_issues"] == 0
    assert summary["by_severity"] == {
        "critical": 0,
        "serious": 0,
        "moderate": 0,
        "minor": 0,
    }


def test_severity_penalties_applied():
    issues = [
        {"severity": "critical", "principle": "perceivable"},
        {"severity": "serious", "principle": "perceivable"},
        {"severity": "moderate", "principle": "operable"},
        {"severity": "minor", "principle": "robust"},
    ]
    summary = calculate_scores(issues)
    # 100 - 8 - 4 - 2 - 1 = 85
    assert summary["score"] == 85
    assert summary["grade"] == "B"
    assert summary["total_issues"] == 4
    assert summary["by_principle"]["perceivable"]["issues"] == 2
    # perceivable penalty = 8 + 4 = 12
    assert summary["by_principle"]["perceivable"]["score"] == 88


def test_score_floors_at_zero():
    issues = [{"severity": "critical", "principle": "robust"}] * 50
    summary = calculate_scores(issues)
    assert summary["score"] == 0
    assert summary["grade"] == "F"


def test_unknown_severity_counts_as_minor():
    issues = [{"severity": "weird", "principle": "operable"}]
    summary = calculate_scores(issues)
    assert summary["score"] == 99
