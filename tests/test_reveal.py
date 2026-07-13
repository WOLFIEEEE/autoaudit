"""Unit tests for audit/reveal.py.

Both layers expose pure analysers over JS-extracted snapshots:
  - analyze_triggers  -> Layer 1 disclosure-state (4.1.2)
  - analyze_revealed  -> Layer 2 newly-revealed failures (2.5.8 / 4.1.2)
No browser needed.
"""

from __future__ import annotations

from typing import Any

from audit import reveal


def _trigger(**kw: Any) -> dict[str, Any]:
    base = {
        "tag": "button", "role": None, "name": "Menu",
        "aria_expanded": None, "aria_controls": "nav1",
        "aria_haspopup": None, "controls_exists": True,
        "controls_hidden": True, "classes": "nav-toggle",
        "interactive": True, "navigates": False, "visible": True,
        "selector": "#menuBtn", "html": "<button>",
    }
    base.update(kw)
    return base


# ---- Layer 1: disclosure state -------------------------------------


def test_toggle_missing_aria_expanded_fires():
    issues = reveal.analyze_triggers([_trigger()])
    assert len(issues) == 1
    assert issues[0]["rule"] == "disclosure-missing-expanded-state"
    assert issues[0]["wcag_criteria"] == ["4.1.2"]
    # aria-controls -> hidden target is the strong signal.
    assert issues[0]["confidence"] == "medium"


def test_class_only_toggle_is_low_confidence():
    issues = reveal.analyze_triggers([_trigger(
        aria_controls=None, controls_exists=False, controls_hidden=False,
        classes="hamburger",
    )])
    assert len(issues) == 1
    assert issues[0]["confidence"] == "low"


def test_toggle_with_aria_expanded_does_not_fire():
    assert reveal.analyze_triggers([_trigger(aria_expanded="false")]) == []


def test_non_toggle_interactive_does_not_fire():
    # A plain button with no toggle class and no hidden controlled region.
    assert reveal.analyze_triggers([_trigger(
        aria_controls=None, controls_exists=False, controls_hidden=False,
        classes="cta-primary", name="Buy",
    )]) == []


def test_invisible_or_noninteractive_skipped():
    assert reveal.analyze_triggers([_trigger(visible=False)]) == []
    assert reveal.analyze_triggers([_trigger(interactive=False)]) == []


def test_controls_target_visible_not_flagged():
    # aria-controls points at an already-visible region (no class signal)
    # -> not a collapsed disclosure, don't flag.
    assert reveal.analyze_triggers([_trigger(
        controls_hidden=False, classes="widget",
    )]) == []


# ---- Layer 2: revealed-element analysis ----------------------------


def _el(selector, w=40, h=40, name="X", interactive=True, inline=False, tag="button"):
    return {
        "selector": selector, "tag": tag, "w": w, "h": h, "name": name,
        "interactive": interactive, "inline_exception": inline,
        "html": f"<{tag}>",
    }


def test_revealed_undersized_target_fires():
    before = [_el("#a")]
    after = [_el("#a"), _el("#dot", w=10, h=10, name="Slide 2")]
    issues = reveal.analyze_revealed(before, after, trigger_name="Next")
    assert any(i["rule"] == "reveal-undersized-target" for i in issues)


def test_revealed_unnamed_control_fires():
    before = []
    after = [_el("#x", w=40, h=40, name="")]
    issues = reveal.analyze_revealed(before, after)
    assert any(i["rule"] == "reveal-control-unnamed" for i in issues)


def test_already_visible_element_not_reported():
    # Present in `before` -> not newly revealed, even if undersized.
    before = [_el("#dot", w=10, h=10, name="")]
    after = [_el("#dot", w=10, h=10, name="")]
    assert reveal.analyze_revealed(before, after) == []


def test_inline_link_exception_not_undersized():
    before = []
    after = [_el("#lnk", w=30, h=12, name="more", inline=True, tag="a")]
    issues = reveal.analyze_revealed(before, after)
    assert not any(i["rule"] == "reveal-undersized-target" for i in issues)


def test_well_sized_named_revealed_control_is_clean():
    before = []
    after = [_el("#ok", w=44, h=44, name="Close")]
    assert reveal.analyze_revealed(before, after) == []


# ---- focus-trap recipe (pure decision logic) -----------------------


def test_focus_trap_escaped_when_focus_leaves():
    # Focus stayed in for 2 tabs, then reached the page behind.
    assert reveal.analyze_focus_trap([True, True, False]) is True


def test_focus_trap_not_escaped_when_focus_stays():
    assert reveal.analyze_focus_trap([True, True, True]) is False


def test_focus_trap_empty_is_not_escaped():
    assert reveal.analyze_focus_trap([]) is False


def test_modal_like_fixed_overlay_covering_page():
    assert reveal._is_modal_like("fixed", 390, 700, 390, 844, False) is True


def test_modal_like_backdrop_forces_true():
    # Even a small region is modal when a backdrop overlay is present.
    assert reveal._is_modal_like("static", 200, 200, 1280, 800, True) is True


def test_not_modal_like_inline_dropdown():
    # Static positioning, no backdrop, small → non-modal, no trap needed.
    assert reveal._is_modal_like("static", 200, 150, 1280, 800, False) is False


def test_not_modal_like_positioned_but_small():
    # Absolutely-positioned tooltip-size popover is not a modal.
    assert reveal._is_modal_like("absolute", 180, 90, 1280, 800, False) is False


def test_menu_class_matches_nav_not_accordion():
    assert reveal._is_menu_classed("navbar-toggle") is True
    assert reveal._is_menu_classed("hamburger") is True
    assert reveal._is_menu_classed("offcanvas-toggle") is True
    # Accordions reflow content — not overlay menus, so excluded.
    assert reveal._is_menu_classed("accordion-toggle") is False


# ---- carousel auto-advance recipe (pure decision logic) ------------


def test_carousel_auto_advance_detected():
    # Slide signature changed at the 3rd sample (index 2) → ~1.0s at 0.5s poll.
    res = reveal.analyze_carousel_samples(["0|0|", "0|0|", "1|0|", "1|0|"], 0.5)
    assert res["auto_advance"] is True
    assert res["interval_s"] == 1.0


def test_carousel_static_not_flagged():
    res = reveal.analyze_carousel_samples(["0|0|", "0|0|", "0|0|"], 0.5)
    assert res["auto_advance"] is False
    assert res["interval_s"] is None


def test_carousel_single_sample_not_flagged():
    assert reveal.analyze_carousel_samples(["0|0|"], 0.5)["auto_advance"] is False


def test_carousel_empty_samples():
    assert reveal.analyze_carousel_samples([], 0.5)["auto_advance"] is False


# ---- carousel structure: region name (1.3.1) + dot size (2.5.8) -----


def test_carousel_unlabeled_region_fires():
    issues = reveal.analyze_carousel_structure([
        {"selector": ".slider1", "has_label": False, "has_heading": False, "dots": []}
    ])
    assert any(i["rule"] == "carousel-region-no-name" for i in issues)


def test_carousel_labeled_region_does_not_fire():
    issues = reveal.analyze_carousel_structure([
        {"selector": ".c", "has_label": True, "has_heading": False, "dots": []}
    ])
    assert not any(i["rule"] == "carousel-region-no-name" for i in issues)
    # A heading inside also counts as a name.
    issues2 = reveal.analyze_carousel_structure([
        {"selector": ".c", "has_label": False, "has_heading": True, "dots": []}
    ])
    assert not any(i["rule"] == "carousel-region-no-name" for i in issues2)


def test_carousel_undersized_dots_fire():
    issues = reveal.analyze_carousel_structure([
        {"selector": ".c", "has_label": True, "has_heading": False,
         "dots": [{"w": 10, "h": 10, "selector": ".bullet", "html": "<div>"},
                  {"w": 10, "h": 10, "selector": ".bullet2", "html": "<div>"}]}
    ])
    dot = [i for i in issues if i["rule"] == "carousel-control-undersized"]
    assert len(dot) == 1
    assert dot[0]["wcag_criteria"] == ["2.5.8"]
    assert dot[0]["details"]["undersized_count"] == 2


def test_carousel_adequate_dots_do_not_fire():
    issues = reveal.analyze_carousel_structure([
        {"selector": ".c", "has_label": True, "has_heading": False,
         "dots": [{"w": 44, "h": 44, "focusable": True, "selector": ".b", "html": "<button>"}]}
    ])
    assert not any(i["rule"] == "carousel-control-undersized" for i in issues)


def test_carousel_non_focusable_dots_fire_keyboard():
    issues = reveal.analyze_carousel_structure([
        {"selector": ".c", "has_label": True, "has_heading": False,
         "dots": [{"w": 10, "h": 10, "focusable": False, "selector": ".b1", "html": "<div>"},
                  {"w": 10, "h": 10, "focusable": False, "selector": ".b2", "html": "<div>"}]}
    ])
    kb = [i for i in issues if i["rule"] == "carousel-control-not-keyboard"]
    assert len(kb) == 1
    assert kb[0]["wcag_criteria"] == ["2.1.1"]


def test_carousel_focusable_dots_no_keyboard_finding():
    issues = reveal.analyze_carousel_structure([
        {"selector": ".c", "has_label": True, "has_heading": False,
         "dots": [{"w": 30, "h": 30, "focusable": True, "selector": ".b1", "html": "<button>"},
                  {"w": 30, "h": 30, "focusable": True, "selector": ".b2", "html": "<button>"}]}
    ])
    assert not any(i["rule"] == "carousel-control-not-keyboard" for i in issues)


# ---- carousel announcement recipe (4.1.3) --------------------------


def test_carousel_change_not_announced():
    # Slide changed at sample 2, but live region stayed the same → silent.
    slides = ["0|0|", "0|0|", "1|0|"]
    live = ["", "", ""]
    res = reveal.analyze_carousel_announcement(slides, live)
    assert res["changed"] is True
    assert res["announced"] is False


def test_carousel_change_announced():
    # Live region updated at the same sample the slide changed.
    slides = ["0|0|", "0|0|", "1|0|"]
    live = ["", "", "Slide 2 of 3~"]
    res = reveal.analyze_carousel_announcement(slides, live)
    assert res["changed"] is True
    assert res["announced"] is True


def test_carousel_no_change_no_announcement_verdict():
    res = reveal.analyze_carousel_announcement(["0|0|", "0|0|"], ["", ""])
    assert res["changed"] is False
    assert res["announced"] is None


# ---- keyboard-operability recipe (2.1.1) ---------------------------


def test_keyboard_inoperable_when_mouse_reveals_keyboard_does_not():
    assert reveal.analyze_keyboard_operable({"#menu-item"}, set()) is True


def test_keyboard_operable_when_both_reveal():
    assert reveal.analyze_keyboard_operable({"#menu-item"}, {"#menu-item"}) is False


def test_keyboard_operable_when_click_revealed_nothing():
    # Not a disclosure trigger → no 2.1.1 verdict.
    assert reveal.analyze_keyboard_operable(set(), set()) is False


# ---- hover-only submenu recipe (2.1.1) -----------------------------


def test_hover_only_menu_fires():
    # Appears on hover, not on focus → keyboard-inaccessible.
    assert reveal.analyze_hover_menu(focus_reveals=False, hover_reveals=True) is True


def test_focusable_menu_does_not_fire():
    # Appears on focus too → keyboard users can reach it.
    assert reveal.analyze_hover_menu(focus_reveals=True, hover_reveals=True) is False


def test_never_revealed_menu_does_not_fire():
    assert reveal.analyze_hover_menu(focus_reveals=False, hover_reveals=False) is False


def test_is_custom_trigger():
    assert reveal._is_custom_trigger({"tag": "div", "role": None}) is True
    assert reveal._is_custom_trigger({"tag": "span", "role": None}) is True
    assert reveal._is_custom_trigger({"tag": "a", "role": "button"}) is True
    # Native controls are keyboard-operable by definition → not tested.
    assert reveal._is_custom_trigger({"tag": "button", "role": None}) is False
    assert reveal._is_custom_trigger({"tag": "a", "role": None}) is False


# ---- run() wiring --------------------------------------------------


class _FakePage:
    def __init__(self, triggers, raise_exc=False):
        self._t = triggers
        self._raise = raise_exc

    def evaluate(self, js=None, *a, **k):  # noqa: ARG002
        if self._raise:
            raise RuntimeError("boom")
        # Only the trigger-discovery probe returns triggers; the carousel
        # structure probe (also run in Layer 1) returns nothing here.
        if js is reveal._DISCOVER_JS:
            return self._t
        return []


def test_run_layer1_only_without_reveal_option():
    page = _FakePage([_trigger()])
    out = reveal.run(page, {})  # no reveal option
    assert out["ran"] is True
    assert out["actuated"] is False
    assert out["triggers_found"] == 1
    assert len(out["issues"]) == 1


def test_run_fails_closed_on_probe_error():
    out = reveal.run(_FakePage(None, raise_exc=True), {})
    assert out["ran"] is False
    assert out["issues"] == []
