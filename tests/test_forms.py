from audit.forms import analyze, _needs_autocomplete


def _rules(issues):
    return [i["rule"] for i in issues]


def _control(**overrides):
    base = {
        "tag": "input",
        "type": "text",
        "name": "",
        "id": "",
        "required": False,
        "aria_required": False,
        "aria_invalid": False,
        "aria_describedby": "",
        "autocomplete": "",
        "accessible_name": "Name",
        "selector": "input",
        "html": "<input>",
    }
    base.update(overrides)
    return base


def _dom(controls=None, groups=None, ids=None):
    return {
        "controls": controls or [],
        "groups": groups or [],
        "ids": ids or [],
    }


def test_labeled_control_is_fine():
    assert analyze(_dom(controls=[_control(accessible_name="Email")])) == []


def test_unlabeled_control_flagged():
    issues = analyze(_dom(controls=[_control(accessible_name="")]))
    assert _rules(issues) == ["forms-input-no-label"]
    assert issues[0]["severity"] == "critical"


def test_button_like_inputs_dont_need_label():
    for t in ("submit", "reset", "button", "hidden", "image"):
        dom = _dom(controls=[_control(type=t, accessible_name="")])
        assert analyze(dom) == []


def test_email_type_without_autocomplete_flagged():
    dom = _dom(controls=[_control(type="email", accessible_name="Email", autocomplete="")])
    assert _rules(analyze(dom)) == ["forms-missing-autocomplete"]


def test_email_type_with_autocomplete_ok():
    dom = _dom(
        controls=[_control(type="email", accessible_name="Email", autocomplete="email")]
    )
    assert analyze(dom) == []


def test_name_substring_match_for_autocomplete():
    dom = _dom(
        controls=[
            _control(type="text", name="user_phone", accessible_name="Phone", autocomplete="")
        ]
    )
    assert "forms-missing-autocomplete" in _rules(analyze(dom))


def test_needs_autocomplete_helper():
    assert _needs_autocomplete({"type": "email", "name": "", "id": ""})
    assert _needs_autocomplete({"type": "text", "name": "street-address", "id": ""})
    assert not _needs_autocomplete({"type": "text", "name": "search", "id": "q"})


def test_aria_invalid_without_description():
    dom = _dom(
        controls=[
            _control(
                accessible_name="Email",
                aria_invalid=True,
                aria_describedby="",
            )
        ]
    )
    assert "forms-aria-invalid-no-description" in _rules(analyze(dom))


def test_aria_invalid_with_missing_description_id():
    dom = _dom(
        controls=[
            _control(
                accessible_name="Email",
                aria_invalid=True,
                aria_describedby="err",
            )
        ],
        ids=["other"],  # "err" doesn't exist
    )
    assert "forms-aria-invalid-no-description" in _rules(analyze(dom))


def test_aria_invalid_with_valid_description_ok():
    dom = _dom(
        controls=[
            _control(
                accessible_name="Email",
                aria_invalid=True,
                aria_describedby="err",
            )
        ],
        ids=["err"],
    )
    assert "forms-aria-invalid-no-description" not in _rules(analyze(dom))


def test_radio_group_without_fieldset_flagged():
    dom = _dom(
        groups=[
            {
                "type": "radio",
                "name": "color",
                "members": [
                    {"selector": "input#r1", "html": "<input>"},
                    {"selector": "input#r2", "html": "<input>"},
                ],
                "in_fieldset": False,
                "fieldset_has_legend": False,
                "group_role": False,
            }
        ]
    )
    issues = analyze(dom)
    assert _rules(issues) == ["forms-radio-group-no-fieldset"]
    assert issues[0]["details"]["option_count"] == 2


def test_radio_group_with_fieldset_and_legend_ok():
    dom = _dom(
        groups=[
            {
                "type": "radio",
                "name": "color",
                "members": [{"selector": "input", "html": "<input>"}] * 2,
                "in_fieldset": True,
                "fieldset_has_legend": True,
                "group_role": False,
            }
        ]
    )
    assert analyze(dom) == []


def test_radio_group_with_radiogroup_role_ok():
    dom = _dom(
        groups=[
            {
                "type": "radio",
                "name": "color",
                "members": [{"selector": "input", "html": "<input>"}] * 2,
                "in_fieldset": False,
                "fieldset_has_legend": False,
                "group_role": True,
            }
        ]
    )
    assert analyze(dom) == []
