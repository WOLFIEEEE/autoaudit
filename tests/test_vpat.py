"""Regression tests for audit/vpat.py.

build_vpat / render_vpat_html previously had no coverage and crashed on
the OBSOLETE 4.1.1 entry. These tests lock the conformance mapping
(including the ai_assisted tier) and the OBSOLETE-skip.
"""

from __future__ import annotations

from audit import vpat


def _audit(issues=None, score=100):
    return {
        "url": "http://example.com",
        "timestamp": "2026-01-01T00:00:00Z",
        "issues": issues or [],
        "summary": {"score": score},
    }


def test_build_vpat_runs_without_obsolete_crash():
    v = vpat.build_vpat(_audit(), target_level="AA")
    assert v["rows"], "expected conformance rows"
    # 4.1.1 Parsing is obsolete in WCAG 2.2 — it must not appear.
    assert not any(r["sc"] == "4.1.1" for r in v["rows"])


def test_ai_assisted_sc_is_partial_without_findings():
    """An ai_assisted SC with no issues must NOT claim Supports — the
    model's judgement requires human confirmation."""
    v = vpat.build_vpat(_audit(), target_level="AA")
    row = next(r for r in v["rows"] if r["sc"] == "1.1.1")
    assert row["coverage_tier"] == "ai_assisted"
    assert row["conformance"] == vpat.PARTIAL


def test_high_confidence_finding_is_does_not_support():
    issues = [{
        "wcag_criteria": ["1.1.1"],
        "confidence": "high",
        "severity": "serious",
    }]
    v = vpat.build_vpat(_audit(issues=issues), target_level="AA")
    row = next(r for r in v["rows"] if r["sc"] == "1.1.1")
    assert row["conformance"] == vpat.DOES_NOT_SUPPORT


def test_render_vpat_html_smoke():
    html = vpat.render_vpat_html(_audit(), target_level="AA")
    assert "<table" in html.lower()
    assert "1.1.1" in html


def test_summary_counts_sum_to_row_total():
    v = vpat.build_vpat(_audit(), target_level="AA")
    counts = v["summary_counts"]
    assert sum(counts.values()) == len(v["rows"])
