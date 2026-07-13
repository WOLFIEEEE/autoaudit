from audit.scorer import calculate_scores
from audit._issue import make_issue


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


def _critical_rule_instances(rule: str, count: int) -> list[dict]:
    """Build `count` copies of the same (rule, fingerprint) so the
    scorer groups them — mimics one design-system defect echoed N times."""
    return [
        make_issue(
            issue_id=f"{rule}-{i}",
            module="test",
            rule=rule,
            severity="critical",
            wcag=["1.4.3"],
            title="",
            selector="#shared",
            html_snippet="<div id='shared'/>",
        )
        for i in range(count)
    ]


def test_log_scaling_collapses_repeated_instances():
    """10 instances of the same defect shouldn't punish 10× a single
    instance. log2(10)+1 ≈ 4.3 — the scorer should see ~34pts, not 80."""
    ten = _critical_rule_instances("color-contrast", 10)
    summary = calculate_scores(ten)
    # Singleton would be 100 - 8 = 92. Ten instances: 8 * (1 + log2(10))
    # ≈ 8 * 4.32 ≈ 34.6 → round → 35 penalty → 65.
    assert 60 <= summary["score"] <= 70, summary["score"]
    assert summary["distinct_defects"] == 1
    assert summary["total_issues"] == 10


def test_confidence_weighting_scales_penalty():
    """A low-confidence serious issue should score ~half of a
    high-confidence one."""
    high = [
        make_issue(
            issue_id="h", module="m", rule="r-h", severity="serious",
            wcag=["1.4.3"], title="", selector="#h", html_snippet="<a/>",
            confidence="high",
        )
    ]
    low = [
        make_issue(
            issue_id="l", module="m", rule="r-l", severity="serious",
            wcag=["1.4.3"], title="", selector="#l", html_snippet="<a/>",
            confidence="low",
        )
    ]
    # High: 100 - 4 = 96. Low: 100 - 2 = 98.
    assert calculate_scores(high)["score"] == 96
    assert calculate_scores(low)["score"] == 98


def test_weakest_principle_is_reported():
    """Issues span two principles; the weaker score surfaces as headline."""
    issues = [
        make_issue(
            issue_id="p1", module="m", rule="perceivable-rule",
            severity="critical", wcag=["1.4.3"], title="",
            selector="#p", html_snippet="<p/>",
        ),
        make_issue(
            issue_id="o1", module="m", rule="operable-rule",
            severity="minor", wcag=["2.1.1"], title="",
            selector="#o", html_snippet="<o/>",
        ),
    ]
    summary = calculate_scores(issues)
    # Perceivable takes an 8-point hit, operable takes 1 — perceivable
    # is weaker.
    assert summary["weakest_principle"] == "perceivable"


def test_weakest_principle_is_none_when_no_issues():
    summary = calculate_scores([])
    assert summary["weakest_principle"] is None


def test_by_confidence_breakdown_present():
    issues = [
        make_issue(
            issue_id="h", module="m", rule="r", severity="minor",
            wcag=["1.4.3"], title="", selector="#x", html_snippet="<x/>",
            confidence="high",
        ),
        make_issue(
            issue_id="l", module="m", rule="r2", severity="minor",
            wcag=["1.4.3"], title="", selector="#y", html_snippet="<y/>",
            confidence="low",
        ),
    ]
    summary = calculate_scores(issues)
    assert summary["by_confidence"]["high"] == 1
    assert summary["by_confidence"]["low"] == 1


def test_unmapped_principle_accounts_for_every_issue():
    """Issues with an unknown principle should flow into 'unmapped' so
    the principle buckets sum to total_issues."""
    issues = [
        make_issue(
            issue_id="ok", module="m", rule="r", severity="minor",
            wcag=["1.4.3"], title="", selector="#x", html_snippet="<x/>",
        ),
        # principle="mystery" → not in PRINCIPLES → should land in unmapped.
        {
            "rule": "r-weird", "severity": "minor",
            "principle": "mystery", "confidence": "high",
        },
    ]
    summary = calculate_scores(issues)
    bp = summary["by_principle"]
    counted = sum(v["issues"] for v in bp.values())
    assert counted == summary["total_issues"]
    assert bp.get("unmapped", {}).get("issues") == 1


def test_distinct_defects_counts_unique_fingerprint_rule_pairs():
    """Three identical (rule, fingerprint) + one different = 2 distinct."""
    a = _critical_rule_instances("r-a", 3)
    b = _critical_rule_instances("r-b", 1)
    summary = calculate_scores(a + b)
    assert summary["distinct_defects"] == 2
    assert summary["total_issues"] == 4


def test_clean_automated_result_does_not_claim_wcag_conformance():
    summary = calculate_scores([])
    conformance = summary["conformance"]

    assert conformance["status"] == "not_determined"
    assert conformance["manual_review_required"] is True
    assert conformance["no_detected_failures"] == {
        "A": True,
        "AA": True,
        "AAA": True,
    }
    assert "A_conformant" not in conformance
