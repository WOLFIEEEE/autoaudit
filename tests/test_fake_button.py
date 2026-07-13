"""Unit tests for audit/fake_button.py (WCAG 2.1.1 / 4.1.2).

Drives the pure `analyze()` over synthetic probe snapshots, covering
both the true positive (div-as-button) and the precision guards that
keep real controls and decorative spans from firing. The false-positive
cases mirror exactly what the live keyboard-practice page exposed.
"""

from __future__ import annotations

from typing import Any

from audit import fake_button


def _cand(**kw: Any) -> dict[str, Any]:
    base = {
        "tag": "div", "role": None, "cursor": "pointer", "focusable": False,
        "classes": "btn btn-primary", "text": "Start free trial",
        "visible": True, "has_focusable_descendant": False,
        "has_interactive_ancestor": False, "selector": "#x", "html": "<div>",
    }
    base.update(kw)
    return base


def test_div_styled_as_button_fires():
    issues = fake_button.analyze({"candidates": [_cand()]})
    assert len(issues) == 1
    iss = issues[0]
    assert iss["rule"] == "fake-button-noninteractive"
    assert iss["wcag_criteria"] == ["2.1.1", "4.1.2"]
    assert iss["severity"] == "serious"
    assert iss["confidence"] == "medium"


def test_pointer_div_without_btn_class_but_with_text_fires():
    # A clickable label div with no btn class still presents as control.
    issues = fake_button.analyze({"candidates": [
        _cand(classes="cta", text="Buy now")
    ]})
    assert len(issues) == 1


def test_focusable_div_does_not_fire():
    # tabindex makes it keyboard-reachable -> different, lesser concern.
    assert fake_button.analyze({"candidates": [_cand(focusable=True)]}) == []


def test_role_button_does_not_fire():
    # Declares an interactive role -> ARIA/widgets module's territory.
    assert fake_button.analyze({"candidates": [_cand(role="button")]}) == []


def test_non_pointer_container_does_not_fire():
    # A btn-row wrapper matches the class but isn't itself clickable.
    assert fake_button.analyze({"candidates": [
        _cand(classes="btn-row", cursor="auto", text="Start free trial Watch demo")
    ]}) == []


def test_clickable_card_with_focusable_descendant_does_not_fire():
    assert fake_button.analyze({"candidates": [
        _cand(classes="card", has_focusable_descendant=True)
    ]}) == []


def test_span_inside_real_link_does_not_fire():
    # The exact false positive the live page produced: logo text spans
    # inheriting pointer cursor from the enclosing <a>.
    assert fake_button.analyze({"candidates": [
        _cand(tag="span", classes="name", text="Accessible",
              has_interactive_ancestor=True)
    ]}) == []


def test_real_anchor_with_href_does_not_fire():
    # A real link is keyboard operable -> out of scope.
    assert fake_button.analyze({"candidates": [
        _cand(tag="a", has_href=True, classes="btn", text="Home")
    ]}) == []


def test_anchor_without_href_fires_as_anchor_rule():
    # barrier #2 on the live page: <a> click-handler control, no href.
    issues = fake_button.analyze({"candidates": [
        _cand(tag="a", has_href=False, classes="btn btn-ghost",
              text="Watch demo")
    ]})
    assert len(issues) == 1
    assert issues[0]["rule"] == "fake-button-anchor-no-href"
    assert issues[0]["wcag_criteria"] == ["2.1.1", "4.1.2"]


def test_anchor_without_href_fires_even_when_focusable():
    # Unlike a div, an href-less anchor is flagged regardless of
    # focusability — the missing href is itself the defect.
    issues = fake_button.analyze({"candidates": [
        _cand(tag="a", has_href=False, focusable=True, text="Watch demo")
    ]})
    assert len(issues) == 1
    assert issues[0]["rule"] == "fake-button-anchor-no-href"


def test_invisible_does_not_fire():
    assert fake_button.analyze({"candidates": [_cand(visible=False)]}) == []


def test_pointer_prose_without_label_or_class_does_not_fire():
    assert fake_button.analyze({"candidates": [
        _cand(tag="p", classes="lead", text="")
    ]}) == []


class _FakePage:
    def __init__(self, payload, raise_exc=False):
        self._p = payload
        self._raise = raise_exc

    def evaluate(self, *a, **k):  # noqa: ARG002
        if self._raise:
            raise RuntimeError("boom")
        return self._p


def test_run_wraps_probe():
    out = fake_button.run(_FakePage({"candidates": [_cand()]}), {})
    assert out["ran"] is True
    assert out["candidate_count"] == 1
    assert len(out["issues"]) == 1


def test_run_fails_closed_on_probe_error():
    out = fake_button.run(_FakePage(None, raise_exc=True), {})
    assert out["ran"] is False
    assert out["issues"] == []
