"""Tests for the screenshot budget/marking logic in audit/screenshots.py.

The image capture itself needs a browser, but the batching policy —
per-rule cap so repetitive rules don't eat the budget, and shared
tally across batches — is pure and worth locking down.
"""

from __future__ import annotations

from typing import Any

from audit import screenshots


class _FakePage:
    """Returns a fake data URI for every _shoot call (via monkeypatch)."""


def _issue(rule: str, sev: str = "serious", sel: str = "#x") -> dict[str, Any]:
    return {"rule": rule, "severity": sev, "element": {"selector": sel}}


def test_per_rule_cap_limits_repetitive_rule(monkeypatch):
    # 10 issues of the same rule → only PER_RULE_SCREENSHOTS get shot.
    monkeypatch.setattr(screenshots, "_shoot", lambda *a, **k: "data:image/png;base64,AAAA")
    issues = [_issue("dup-rule", sel=f"#e{i}") for i in range(10)]
    per_rule: dict[str, int] = {}
    used = screenshots._shoot_batch(_FakePage(), issues, max_shots=30, budget=0, per_rule=per_rule)
    assert used == screenshots.PER_RULE_SCREENSHOTS
    assert per_rule["dup-rule"] == screenshots.PER_RULE_SCREENSHOTS
    shot = sum(1 for i in issues if (i.get("details") or {}).get("screenshot"))
    assert shot == screenshots.PER_RULE_SCREENSHOTS


def test_budget_spreads_across_distinct_rules(monkeypatch):
    monkeypatch.setattr(screenshots, "_shoot", lambda *a, **k: "data:image/png;base64,AAAA")
    # 5 rules x 5 instances; per-rule cap 3 → 5*3 = 15 shots within a 30 budget.
    issues = [_issue(f"rule{r}", sel=f"#r{r}e{i}") for r in range(5) for i in range(5)]
    per_rule: dict[str, int] = {}
    used = screenshots._shoot_batch(_FakePage(), issues, max_shots=30, budget=0, per_rule=per_rule)
    assert used == 5 * screenshots.PER_RULE_SCREENSHOTS
    assert set(per_rule.values()) == {screenshots.PER_RULE_SCREENSHOTS}


def test_max_shots_hard_cap(monkeypatch):
    monkeypatch.setattr(screenshots, "_shoot", lambda *a, **k: "data:image/png;base64,AAAA")
    # Many distinct rules, but max_shots caps the total.
    issues = [_issue(f"rule{i}", sel=f"#e{i}") for i in range(50)]
    per_rule: dict[str, int] = {}
    used = screenshots._shoot_batch(_FakePage(), issues, max_shots=10, budget=0, per_rule=per_rule)
    assert used == 10


def test_missing_selector_skipped(monkeypatch):
    monkeypatch.setattr(screenshots, "_shoot", lambda *a, **k: "data:image/png;base64,AAAA")
    issues = [{"rule": "r", "severity": "serious", "element": {"selector": ""}}]
    per_rule: dict[str, int] = {}
    used = screenshots._shoot_batch(_FakePage(), issues, max_shots=30, budget=0, per_rule=per_rule)
    assert used == 0
