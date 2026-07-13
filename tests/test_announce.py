"""Unit tests for audit/announce.py.

The CDP extraction needs a browser, but the two pure functions —
node -> record and record -> approximate spoken phrase — carry the logic
worth locking down, and run without one.
"""

from __future__ import annotations

from audit import announce


def _ax(role, name="", *, props=None, description="", value=""):
    """Build a minimal CDP AX-tree node dict."""
    return {
        "role": {"value": role},
        "name": {"value": name},
        "description": {"value": description},
        "value": {"value": value},
        "properties": props or [],
        "backendDOMNodeId": 7,
        "ignored": False,
    }


def _prop(name, value):
    return {"name": name, "value": {"value": value}}


# ---- _node_to_record ------------------------------------------------


def test_record_extracts_role_name_states():
    node = _ax("button", "Search", props=[_prop("focusable", True), _prop("expanded", False)])
    rec = announce._node_to_record(node)
    assert rec["role"] == "button"
    assert rec["name"] == "Search"
    assert rec["states"]["focusable"] is True
    assert rec["states"]["expanded"] is False
    assert rec["backend_id"] == 7


def test_record_extracts_heading_level():
    rec = announce._node_to_record(_ax("heading", "Welcome", props=[_prop("level", 1)]))
    assert rec["level"] == 1
    assert "level" not in rec["states"]  # level is not a state


# ---- format_announcement -------------------------------------------


def test_named_link_announced_name_then_role():
    rec = announce._node_to_record(_ax("link", "Home"))
    assert announce.format_announcement(rec) == "Home, link"


def test_unnamed_control_flagged():
    rec = announce._node_to_record(_ax("link", ""))
    assert announce.format_announcement(rec) == "(no accessible name), link"


def test_heading_announced_with_level():
    rec = announce._node_to_record(_ax("heading", "Welcome", props=[_prop("level", 2)]))
    assert announce.format_announcement(rec) == "Welcome, heading level 2"


def test_collapsed_toggle():
    rec = announce._node_to_record(_ax("button", "Menu", props=[_prop("expanded", False)]))
    assert announce.format_announcement(rec) == "Menu, button, collapsed"


def test_checkbox_states():
    checked = announce._node_to_record(_ax("checkbox", "Agree", props=[_prop("checked", True)]))
    assert announce.format_announcement(checked) == "Agree, checkbox, checked"
    unchecked = announce._node_to_record(_ax("checkbox", "Agree", props=[_prop("checked", False)]))
    assert announce.format_announcement(unchecked) == "Agree, checkbox, not checked"


def test_required_disabled_textbox_value():
    rec = announce._node_to_record(_ax(
        "textbox", "Email", props=[_prop("required", True), _prop("disabled", True)], value="a@b.com"
    ))
    ann = announce.format_announcement(rec)
    assert "Email, textbox" in ann
    assert "unavailable" in ann and "required" in ann and "value a@b.com" in ann


def test_non_checkable_false_checked_not_spoken():
    # A link that happens to carry checked=False must not say "not checked".
    rec = announce._node_to_record(_ax("link", "X", props=[_prop("checked", False)]))
    assert announce.format_announcement(rec) == "X, link"
