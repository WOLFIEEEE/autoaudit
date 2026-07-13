"""Unit tests for audit/target_size.py (WCAG 2.5.8).

We don't drive a real browser here — `target_size.run` calls
`page.evaluate(_PROBE_JS, MIN_PX)`, so a fake page that returns
prepared findings is enough to exercise the Python-side filtering,
classification, sorting, and issue construction.

For the JS probe itself, the e2e suite covers it against a live page;
unit-testing query-DOM JavaScript across browsers belongs there.
"""

from __future__ import annotations

from typing import Any

import pytest

from audit import target_size


class _FakePage:
    """Stand-in for Playwright's `page` that returns canned findings.

    The JS probe is a pure function of the DOM, so we just hand the
    Python orchestration code the list it would have received.
    """

    def __init__(self, findings: list[dict[str, Any]]):
        self._findings = findings
        self.calls: list[tuple[str, Any]] = []

    def evaluate(self, script: str, *args: Any) -> Any:  # noqa: ARG002
        self.calls.append(("evaluate", args))
        return self._findings


def _finding(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "selector": "#x",
        "tag": "button",
        "html_snippet": "<button>x</button>",
        "width": 20.0,
        "height": 20.0,
        "accessible_name": "x",
        "spacing_exception_applies": False,
    }
    base.update(overrides)
    return base


def test_no_findings_no_issues():
    page = _FakePage([])
    result = target_size.run(page)
    assert result["ran"] is True
    assert result["issues"] == []
    assert result["candidate_count"] == 0


def test_undersized_without_spacing_is_serious():
    page = _FakePage([_finding(spacing_exception_applies=False)])
    result = target_size.run(page)
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["severity"] == "serious"
    assert issue["rule"] == "target-size-undersized"
    assert "2.5.8" in issue["wcag_criteria"]


def test_undersized_with_spacing_is_moderate():
    """Spacing exception currently applies → still report, lower severity."""
    page = _FakePage([_finding(spacing_exception_applies=True)])
    result = target_size.run(page)
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["severity"] == "moderate"
    assert issue["rule"] == "target-size-spacing-tight"


def test_outright_failures_sort_before_spacing_tight():
    """Hard failures should appear first so the worst rises to the top."""
    page = _FakePage([
        _finding(selector="#a", spacing_exception_applies=True, width=10, height=10),
        _finding(selector="#b", spacing_exception_applies=False, width=18, height=18),
    ])
    result = target_size.run(page)
    rules = [i["rule"] for i in result["issues"]]
    assert rules == ["target-size-undersized", "target-size-spacing-tight"]


def test_truncates_to_max_reported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(target_size, "MAX_REPORTED", 3)
    findings = [_finding(selector=f"#a{i}", width=10, height=10) for i in range(10)]
    page = _FakePage(findings)
    result = target_size.run(page)
    assert len(result["issues"]) == 3
    assert result["candidate_count"] == 10
    assert result["truncated"] is True


def test_evaluate_failure_is_isolated():
    class _BoomPage:
        def evaluate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("playwright disconnected")

    result = target_size.run(_BoomPage())
    assert result["ran"] is False
    assert "playwright" in result["error"].lower()
    assert result["issues"] == []


def test_issue_carries_dimensions_and_min():
    page = _FakePage([_finding(width=12.5, height=8.0)])
    issue = target_size.run(page)["issues"][0]
    assert issue["details"]["width_px"] == 12.5
    assert issue["details"]["height_px"] == 8.0
    assert issue["details"]["minimum_px"] == 24
