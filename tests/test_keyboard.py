from audit.keyboard import analyze


def _rules(issues):
    return [i["rule"] for i in issues]


def _stop(**overrides):
    base = {
        "tag": "button",
        "id": "",
        "selector": "button",
        "role": "",
        "has_role_attr": False,
        "is_semantic_tag": True,
        "tabindex": 0,
        "accessible_name": "Save",
        "outline_style": "solid",
        "outline_width": "2px",
        "box_shadow": "none",
        "has_focus_indicator": True,
        "html": "<button>Save</button>",
    }
    base.update(overrides)
    return base


def test_clean_walk_produces_no_issues():
    stops = [_stop(selector="button#a"), _stop(selector="a#b", accessible_name="Home")]
    assert analyze(stops, cycled=True, max_tabs=100) == []


def test_no_cycle_at_max_tabs_flags_trap():
    stops = [_stop(selector=f"button#b{i}") for i in range(100)]
    issues = analyze(stops, cycled=False, max_tabs=100)
    rules = _rules(issues)
    assert "keyboard-trap-suspected" in rules
    trap = next(i for i in issues if i["rule"] == "keyboard-trap-suspected")
    assert trap["severity"] == "critical"


def test_short_walk_without_cycle_not_a_trap():
    # If we collected fewer stops than max_tabs and didn't cycle, we likely
    # had no more focusable elements — not a trap.
    stops = [_stop(selector="button#only")]
    issues = analyze(stops, cycled=False, max_tabs=100)
    assert "keyboard-trap-suspected" not in _rules(issues)


def test_missing_accessible_name_flagged():
    stops = [_stop(selector="div#x", accessible_name="", tag="div")]
    issues = analyze(stops, cycled=True, max_tabs=100)
    assert _rules(issues) == ["keyboard-no-accessible-name"]
    assert issues[0]["severity"] == "critical"


def test_missing_focus_indicator_flagged():
    stops = [
        _stop(
            selector="a.bare",
            outline_style="none",
            box_shadow="none",
            has_focus_indicator=False,
        )
    ]
    issues = analyze(stops, cycled=True, max_tabs=100)
    assert _rules(issues) == ["keyboard-no-focus-indicator"]


def test_positive_tabindex_flagged():
    stops = [_stop(selector="input#x", tabindex=3)]
    issues = analyze(stops, cycled=True, max_tabs=100)
    assert _rules(issues) == ["keyboard-positive-tabindex"]
    assert issues[0]["details"]["tabindex"] == 3


def test_div_tabindex_zero_flagged_as_generic_focusable():
    # <div tabindex="0"> with no role attribute → "generic focusable" rule.
    stops = [
        _stop(
            tag="div",
            selector="div#trouble",
            is_semantic_tag=False,
            has_role_attr=False,
            accessible_name="Some text",
            html='<div tabindex="0">Some text</div>',
        )
    ]
    issues = analyze(stops, cycled=True, max_tabs=100)
    assert "keyboard-generic-focusable" in _rules(issues)
    gi = next(i for i in issues if i["rule"] == "keyboard-generic-focusable")
    assert gi["severity"] == "serious"


def test_div_with_role_attribute_not_flagged():
    # <div role="button" tabindex="0"> is acceptable — explicit role fixes it.
    stops = [
        _stop(
            tag="div",
            selector="div.custom",
            is_semantic_tag=False,
            has_role_attr=True,
            role="button",
            accessible_name="Custom button",
        )
    ]
    assert "keyboard-generic-focusable" not in _rules(
        analyze(stops, cycled=True, max_tabs=100)
    )


def test_semantic_tag_not_flagged_as_generic():
    # <a href>, <button>, <input> are semantic — never generic.
    for tag in ("a", "button", "input", "select", "textarea"):
        stops = [_stop(tag=tag, is_semantic_tag=True)]
        rules = _rules(analyze(stops, cycled=True, max_tabs=100))
        assert "keyboard-generic-focusable" not in rules, tag


def test_multiple_issues_on_same_element():
    stops = [
        _stop(
            selector="span#trouble",
            tag="span",
            is_semantic_tag=False,
            has_role_attr=False,
            accessible_name="",
            tabindex=5,
            outline_style="none",
            box_shadow="none",
            has_focus_indicator=False,
        )
    ]
    issues = analyze(stops, cycled=True, max_tabs=100)
    # Single element can produce four separate issues.
    assert set(_rules(issues)) == {
        "keyboard-no-accessible-name",
        "keyboard-no-focus-indicator",
        "keyboard-positive-tabindex",
        "keyboard-generic-focusable",
    }
