from audit.aria import analyze


def _rules(issues):
    return [i["rule"] for i in issues]


def test_valid_aria_produces_no_issues():
    dom = {
        "ids": ["label1"],
        "roles": [{"role": "button", "selector": "div.btn", "html": "<div>"}],
        "labelledby": [{"refs": ["label1"], "selector": "input", "html": "<input>"}],
        "describedby": [],
        "hidden_focusable": [],
    }
    assert analyze(dom) == []


def test_invalid_role_flagged():
    dom = {
        "ids": [],
        "roles": [{"role": "buton", "selector": "div", "html": "<div>"}],
        "labelledby": [],
        "describedby": [],
        "hidden_focusable": [],
    }
    assert _rules(analyze(dom)) == ["aria-invalid-role"]


def test_role_token_list_with_one_valid_is_ok():
    # ARIA allows space-separated fallback roles; first valid applies.
    dom = {
        "ids": [],
        "roles": [{"role": "nonsense button", "selector": "div", "html": "<div>"}],
        "labelledby": [],
        "describedby": [],
        "hidden_focusable": [],
    }
    assert analyze(dom) == []


def test_labelledby_missing_id():
    dom = {
        "ids": ["exists"],
        "roles": [],
        "labelledby": [
            {"refs": ["exists", "missing"], "selector": "input", "html": "<input>"},
        ],
        "describedby": [],
        "hidden_focusable": [],
    }
    issues = analyze(dom)
    assert _rules(issues) == ["aria-labelledby-missing"]
    assert issues[0]["details"]["missing_ids"] == ["missing"]


def test_describedby_missing_id_is_moderate():
    dom = {
        "ids": [],
        "roles": [],
        "labelledby": [],
        "describedby": [{"refs": ["nope"], "selector": "input", "html": "<input>"}],
        "hidden_focusable": [],
    }
    issues = analyze(dom)
    assert issues[0]["severity"] == "moderate"


def test_aria_hidden_focusable_flagged():
    dom = {
        "ids": [],
        "roles": [],
        "labelledby": [],
        "describedby": [],
        "hidden_focusable": [
            {"selector": "button", "html": "<button>", "focusable_child": False}
        ],
    }
    assert _rules(analyze(dom)) == ["aria-hidden-focusable"]
