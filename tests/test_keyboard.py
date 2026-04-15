from audit.keyboard import analyze


def _rules(issues):
    return [i["rule"] for i in issues]


def _stop(**overrides):
    base = {
        "tag": "button",
        "id": "",
        "selector": "button",
        "role": "",
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


def test_multiple_issues_on_same_element():
    stops = [
        _stop(
            selector="span#trouble",
            tag="span",
            accessible_name="",
            tabindex=5,
            outline_style="none",
            box_shadow="none",
            has_focus_indicator=False,
        )
    ]
    issues = analyze(stops, cycled=True, max_tabs=100)
    # Single element can produce three separate issues.
    assert set(_rules(issues)) == {
        "keyboard-no-accessible-name",
        "keyboard-no-focus-indicator",
        "keyboard-positive-tabindex",
    }
