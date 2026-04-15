from audit.screen_reader import analyze


def _rules(issues):
    return [i["rule"] for i in issues]


def _tree(children):
    return {"role": "WebArea", "name": "", "children": children}


def test_empty_tree_has_no_issues():
    assert analyze(None) == []
    assert analyze({"role": "WebArea", "name": "", "children": []}) == []


def test_labeled_button_is_fine():
    tree = _tree([{"role": "button", "name": "Save"}])
    assert analyze(tree) == []


def test_unlabeled_button_flagged():
    tree = _tree([{"role": "button", "name": ""}])
    issues = analyze(tree)
    assert _rules(issues) == ["sr-silent-interactive"]
    assert issues[0]["severity"] == "critical"


def test_disabled_unlabeled_button_not_flagged():
    # Disabled controls are commonly intentionally nameless; skip to avoid noise.
    tree = _tree([{"role": "button", "name": "", "disabled": True}])
    assert analyze(tree) == []


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
                        "name": "Inner",
                        "children": [{"role": "button", "name": ""}],
                    }
                ],
            }
        ]
    )
    rules = _rules(analyze(tree))
    # Nested silent button should still be caught.
    assert "sr-silent-interactive" in rules
