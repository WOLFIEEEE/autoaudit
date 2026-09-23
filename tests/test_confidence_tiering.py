"""Confidence must be a claim the corpus supports, not an author's guess.

Before this, `confidence` was a hand-picked string nothing checked, so a
rule nobody had ever measured could ship marked `high`. Since confidence
is a score multiplier, that is an unearned assertion with a number
attached.
"""

from __future__ import annotations

import json

import pytest

from audit import confidence


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Point the module at a registry we control, not the committed one."""
    path = tmp_path / "precision.json"
    path.write_text(json.dumps({
        "measured_over_fixtures": 3,
        "rules": {
            "proven-rule":   {"precision": 1.0,  "tp": 9, "fp": 0},
            "decent-rule":   {"precision": 0.88, "tp": 8, "fp": 1},
            "shaky-rule":    {"precision": 0.55, "tp": 5, "fp": 4},
            "unscored-rule": {"precision": None, "tp": 0, "fp": 0},
        },
    }))
    monkeypatch.setattr(confidence, "_REGISTRY_PATH", path)
    confidence.reset_cache()
    yield
    confidence.reset_cache()


@pytest.mark.parametrize("rule,expected", [
    ("proven-rule", "high"),
    ("decent-rule", "medium"),
    ("shaky-rule", "low"),
    ("unscored-rule", "low"),
    ("rule-nobody-has-ever-measured", "low"),
])
def test_ceiling_tracks_measured_precision(rule, expected):
    assert confidence.ceiling_for(rule) == expected


def test_an_unmeasured_rule_can_never_be_high():
    """The load-bearing rule: no fixture, no confident finding.

    This is what makes the corpus mandatory rather than aspirational, and
    what stops a future rule repeating the browse-mode failure.
    """
    assert confidence.cap("rule-nobody-has-ever-measured", "high") == "low"


def test_confidence_is_only_ever_lowered():
    """An author who marks a rule medium knows something the corpus does
    not. Perfect measured precision must not promote it."""
    assert confidence.cap("proven-rule", "medium") == "medium"
    assert confidence.cap("proven-rule", "low") == "low"
    assert confidence.cap("shaky-rule", "high") == "low"


def test_capping_records_what_it_changed():
    issues = [
        {"rule": "proven-rule", "confidence": "high"},
        {"rule": "shaky-rule", "confidence": "high"},
    ]
    confidence.apply_to_issues(issues)

    assert issues[0]["confidence"] == "high"
    assert "confidence_capped" not in issues[0]

    assert issues[1]["confidence"] == "low"
    assert issues[1]["declared_confidence"] == "high"
    assert issues[1]["confidence_capped"] is True


def test_a_missing_registry_understates_rather_than_invents(tmp_path, monkeypatch):
    """A fresh checkout has no registry. Everything caps to low, which is
    the safe direction — it never asserts confidence it cannot support."""
    monkeypatch.setattr(confidence, "_REGISTRY_PATH", tmp_path / "absent.json")
    confidence.reset_cache()
    assert confidence.cap("proven-rule", "high") == "low"


def test_tiering_is_off_by_default_in_the_orchestrator():
    """With most of the rule set unmeasured, switching this on would
    demote nearly everything and inflate every score. It stays opt-in
    until corpus coverage makes an unmeasured rule the exception."""
    from audit.orchestrator import AuditOrchestrator

    orch = AuditOrchestrator(url="https://example.com", options={})
    issues = [{"rule": "rule-nobody-has-ever-measured", "confidence": "high"}]
    assert orch._apply_confidence_tiering(issues)[0]["confidence"] == "high"

    orch_on = AuditOrchestrator(
        url="https://example.com", options={"confidence_tiering": True}
    )
    issues = [{"rule": "rule-nobody-has-ever-measured", "confidence": "high"}]
    assert orch_on._apply_confidence_tiering(issues)[0]["confidence"] == "low"
