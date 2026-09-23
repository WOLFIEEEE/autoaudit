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


# ---------------------------------------------------------------------------
# Browse mode (Path B)


def _browse(utterances, nodes):
    return {"ran": True, "utterances": utterances, "visible_text_nodes": nodes}


def test_browse_mode_empty_transcript_emits_nothing():
    """A degenerate walk must never be read as "the page hid all its text".

    When NVDA produces no speech we cannot tell a catastrophically broken
    page from keystrokes that never reached NVDA. Emitting skipped-text
    for every node here flagged clean pages with 20 "serious" failures.
    """
    from audit.screen_reader import analyze_browse_mode

    nodes = [
        {"text": "Welcome to the store", "aria_hidden": False},
        {"text": "Add to basket", "aria_hidden": False},
        {"text": "Free delivery over 50", "aria_hidden": False},
    ]
    assert analyze_browse_mode(_browse([], nodes)) == []


def test_browse_mode_flags_genuinely_skipped_text():
    """With a real transcript the rule still fires for unspoken text."""
    from audit.screen_reader import analyze_browse_mode

    nodes = [
        {"text": "Welcome to the store", "aria_hidden": False},
        {"text": "Rendered via icon font", "aria_hidden": False},
    ]
    issues = analyze_browse_mode(
        _browse(["heading level 1, Welcome to the store"], nodes)
    )
    rules = [i["rule"] for i in issues]
    assert rules == ["sr-browse-skipped-text"]
    assert issues[0]["details"]["text"] == "Rendered via icon font"


def test_browse_mode_flags_aria_hidden_leakage():
    from audit.screen_reader import analyze_browse_mode

    nodes = [{"text": "decorative chevron glyph", "aria_hidden": True}]
    issues = analyze_browse_mode(
        _browse(["decorative chevron glyph"], nodes)
    )
    assert [i["rule"] for i in issues] == ["sr-browse-decorative-noise"]


def test_browse_mode_issue_ids_are_stable_across_processes():
    """issue_id must not depend on PYTHONHASHSEED.

    Builtin hash() for str is salted per process, so ids derived from it
    changed on every run and any consumer keying on issue_id re-created
    the same finding as new each audit.
    """
    import subprocess
    import sys

    snippet = (
        "import json,sys;"
        "sys.path.insert(0, '.');"
        "from audit.screen_reader import analyze_browse_mode;"
        "r=analyze_browse_mode({'ran':True,'utterances':['spoken bit'],"
        "'visible_text_nodes':[{'text':'never spoken here','aria_hidden':False}]});"
        "print(r[0]['id'])"
    )
    ids = {
        subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        for _ in range(3)
    }
    assert len(ids) == 1, f"issue_id is not deterministic across runs: {ids}"
