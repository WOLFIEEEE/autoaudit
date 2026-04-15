"""Invariant test: every issue any module produces must have a `principle`
that matches the WCAG mapping. Catches regressions if someone ever
re-adds a hand-assigned principle that contradicts the spec.

Runs each module's analyze() with representative fixture data that
produces at least one issue, then checks every output.
"""

from __future__ import annotations

from audit._wcag import DEFAULT_PRINCIPLE, principle_for


def _check(issues):
    for issue in issues:
        derived = principle_for(issue.get("wcag_criteria") or [])
        # If wcag_criteria is empty the default principle wins — that's OK.
        if not issue.get("wcag_criteria"):
            assert issue["principle"] == DEFAULT_PRINCIPLE
        else:
            assert issue["principle"] == derived, (
                f"rule {issue['rule']!r} declares principle={issue['principle']!r} "
                f"but wcag_criteria={issue['wcag_criteria']} implies {derived!r}"
            )


def test_structure_principles():
    from audit.structure import analyze

    dom = {
        "lang": "",
        "title": "",
        "headings": [
            {"level": 1, "text": "x", "selector": "h1", "html": ""},
            {"level": 3, "text": "y", "selector": "h3", "html": ""},
            {"level": 1, "text": "z", "selector": "h1:nth-of-type(2)", "html": ""},
        ],
        "landmarks": {"main": 0},
        "tables": [{"has_th": False, "has_caption": False, "selector": "t", "html": ""}],
    }
    _check(analyze(dom))


def test_aria_principles():
    from audit.aria import analyze

    dom = {
        "ids": ["ok"],
        "roles": [{"role": "buton", "selector": "x", "html": ""}],
        "labelledby": [{"refs": ["missing"], "selector": "x", "html": ""}],
        "describedby": [{"refs": ["nope"], "selector": "x", "html": ""}],
        "hidden_focusable": [{"selector": "x", "html": "", "focusable_child": False}],
    }
    _check(analyze(dom))


def test_media_principles():
    from audit.media import analyze

    dom = {
        "images": [
            {"alt": None, "src": "x", "role": None, "aria_hidden": False, "selector": "i", "html": ""},
            {"alt": "photo.jpg", "src": "x", "role": None, "aria_hidden": False, "selector": "i", "html": ""},
            {"alt": "foo", "src": "x", "role": "presentation", "aria_hidden": False, "selector": "i", "html": ""},
        ],
        "videos": [
            {"has_caption_track": False, "autoplay": True, "muted": False, "selector": "v", "html": ""}
        ],
        "audios": [{"autoplay": True, "muted": False, "selector": "a", "html": ""}],
    }
    _check(analyze(dom))


def test_cognitive_principles():
    from audit.cognitive import analyze

    links = [
        {"text": "", "href": "/x", "selector": "a", "html": ""},
        {"text": "click here", "href": "/y", "selector": "a", "html": ""},
        {"text": "doc", "href": "/v1", "selector": "a", "html": ""},
        {"text": "doc", "href": "/v2", "selector": "a", "html": ""},
    ]
    _check(analyze(links))


def test_visual_principles():
    from audit.visual import analyze

    dom = {
        "marquee": [{"tag": "marquee", "selector": "m", "html": ""}],
        "infinite_animations": [
            {"tag": "div", "selector": "d", "html": "", "animation_name": "s", "duration_s": 2.0}
        ],
        "tiny_text": [{"selector": "p", "html": "", "font_size_px": 7, "tag": "p"}],
    }
    _check(analyze(dom))


def test_keyboard_principles():
    from audit.keyboard import analyze

    stops = [
        {
            "tag": "div",
            "id": "",
            "selector": "d",
            "role": "",
            "has_role_attr": False,
            "is_semantic_tag": False,
            "tabindex": 3,
            "accessible_name": "",
            "outline_style": "none",
            "outline_width": "0",
            "box_shadow": "none",
            "has_focus_indicator": False,
            "html": "",
        }
    ]
    _check(analyze(stops, cycled=True, max_tabs=100))


def test_forms_principles():
    from audit.forms import analyze

    dom = {
        "controls": [
            {
                "tag": "input",
                "type": "email",
                "name": "",
                "id": "",
                "required": False,
                "aria_required": False,
                "aria_invalid": True,
                "aria_describedby": "",
                "autocomplete": "",
                "accessible_name": "",
                "selector": "i",
                "html": "",
            }
        ],
        "groups": [
            {
                "type": "radio",
                "name": "c",
                "members": [{"selector": "r", "html": ""}] * 2,
                "in_fieldset": False,
                "fieldset_has_legend": False,
                "group_role": False,
            }
        ],
        "ids": [],
    }
    _check(analyze(dom))


def test_responsive_principles():
    from audit.responsive import analyze

    dom = {
        "viewport": {"present": True, "content": "user-scalable=no"},
        "targets": [
            {
                "tag": "button",
                "type": "",
                "role": "",
                "width": 10,
                "height": 10,
                "display": "inline-block",
                "visibility": "visible",
                "offscreen": False,
                "selector": "b",
                "html": "",
            }
        ],
    }
    _check(analyze(dom))


def test_screen_reader_principles():
    from audit.screen_reader import analyze

    tree = {
        "role": "WebArea",
        "name": "",
        "children": [
            {"role": "button", "name": ""},
            {"role": "heading", "name": "", "level": 2},
            {"role": "dialog", "name": ""},
            {"role": "navigation", "name": ""},
            {"role": "navigation", "name": ""},
        ],
    }
    _check(analyze(tree))
