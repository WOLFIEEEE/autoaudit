from audit.screen_reader import analyze


def _rules(issues):
    return [i["rule"] for i in issues]


def _tree(children):
    return {"role": "WebArea", "name": "", "children": children}


def test_empty_tree_has_no_issues():
    assert analyze(None) == []
    assert analyze({"role": "WebArea", "name": "", "children": []}) == []


def test_labeled_button_is_fine():
    tree = _tree([{"role": "button", "name": "Save", "focusable": True}])
    assert analyze(tree) == []


def test_unlabeled_button_flagged():
    tree = _tree([{"role": "button", "name": "", "focusable": True}])
    issues = analyze(tree)
    assert _rules(issues) == ["sr-silent-focusable"]
    assert issues[0]["severity"] == "critical"


def test_non_focusable_unlabeled_button_not_flagged():
    # Non-focusable buttons (e.g. aria-hidden ones) don't trip this rule.
    tree = _tree([{"role": "button", "name": "", "focusable": False}])
    assert analyze(tree) == []


def test_focusable_generic_flagged():
    tree = _tree([{"role": "generic", "name": "Click me", "focusable": True}])
    rules = _rules(analyze(tree))
    assert "sr-generic-interactive" in rules


def test_div_tabindex_without_role_detected():
    # Chromium exposes <div tabindex=0> as role=generic.
    tree = _tree(
        [
            {
                "role": "generic",
                "name": "",
                "focusable": True,
                "children": [],
            }
        ]
    )
    rules = _rules(analyze(tree))
    assert "sr-generic-interactive" in rules
    # And it's also silent, so both rules fire on the same node.
    assert "sr-silent-focusable" not in rules  # generic isn't an interactive role


def test_empty_heading_flagged():
    tree = _tree([{"role": "heading", "name": "", "level": 2}])
    assert _rules(analyze(tree)) == ["sr-empty-heading"]


def test_heading_with_text_is_fine():
    tree = _tree([{"role": "heading", "name": "Introduction", "level": 1}])
    assert analyze(tree) == []


def test_dialog_no_name_flagged():
    tree = _tree([{"role": "dialog", "name": "", "children": []}])
    assert _rules(analyze(tree)) == ["sr-dialog-no-name"]


def test_alertdialog_no_name_flagged():
    tree = _tree([{"role": "alertdialog", "name": ""}])
    assert _rules(analyze(tree)) == ["sr-dialog-no-name"]


def test_dialog_with_name_ok():
    tree = _tree([{"role": "dialog", "name": "Confirm delete"}])
    assert analyze(tree) == []


def test_duplicate_landmarks_with_no_names_flagged():
    tree = _tree(
        [
            {"role": "navigation", "name": ""},
            {"role": "navigation", "name": ""},
        ]
    )
    issues = analyze(tree)
    assert _rules(issues) == ["sr-duplicate-landmark"]
    # Only the second (duplicate) is reported.
    assert issues[0]["details"]["count"] == 2


def test_distinct_landmark_names_ok():
    tree = _tree(
        [
            {"role": "navigation", "name": "Primary"},
            {"role": "navigation", "name": "Footer"},
        ]
    )
    assert analyze(tree) == []


def test_three_duplicate_landmarks_report_two():
    tree = _tree(
        [
            {"role": "navigation", "name": "Main"},
            {"role": "navigation", "name": "Main"},
            {"role": "navigation", "name": "Main"},
        ]
    )
    issues = analyze(tree)
    dup = [i for i in issues if i["rule"] == "sr-duplicate-landmark"]
    # Three copies with same name → two duplicate reports (2nd and 3rd).
    assert len(dup) == 2


def test_nested_tree_is_walked():
    tree = _tree(
        [
            {
                "role": "main",
                "name": "Content",
                "children": [
                    {
                        "role": "region",
                        "name": "",
                        "children": [
                            {"role": "button", "name": "", "focusable": True}
                        ],
                    }
                ],
            }
        ]
    )
    rules = _rules(analyze(tree))
    # The deeply-nested silent button should still be found.
    assert "sr-silent-focusable" in rules


def test_multiple_rules_on_same_element():
    # Focusable + generic role + no name → generic fires (interactive rule
    # only fires for the semantic interactive-role list).
    tree = _tree([{"role": "generic", "name": "", "focusable": True}])
    rules = set(_rules(analyze(tree)))
    assert rules == {"sr-generic-interactive"}
