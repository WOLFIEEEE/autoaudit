"""Unit tests for tag-parsing helpers in audit.wcag_engine.

The Playwright-driven integration test lives separately (not included
in the default suite because it needs a browser binary).
"""

from audit.wcag_engine import (
    _principle_from_tags,
    _wcag_criteria_from_tags,
)


def test_principle_from_wcag_tag():
    assert _principle_from_tags(["wcag143", "wcag2aa"]) == "perceivable"
    assert _principle_from_tags(["wcag211"]) == "operable"
    assert _principle_from_tags(["wcag311"]) == "understandable"
    assert _principle_from_tags(["wcag412"]) == "robust"


def test_principle_defaults_to_robust():
    assert _principle_from_tags([]) == "robust"
    assert _principle_from_tags(["best-practice"]) == "robust"


def test_wcag_criteria_parsed():
    assert _wcag_criteria_from_tags(["wcag143"]) == ["1.4.3"]
    # Mixed input — only wcag tags contribute.
    assert _wcag_criteria_from_tags(["wcag111", "best-practice", "wcag412"]) == [
        "1.1.1",
        "4.1.2",
    ]
