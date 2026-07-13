from audit.deduplicator import (
    CROSS_MODULE_ALIASES,
    deduplicate_issues,
    validate_cross_module_aliases,
)
from audit._issue import make_issue


def test_empty_input():
    assert deduplicate_issues([]) == []


def test_unique_issues_passthrough():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#a"}},
        {"rule": "label-missing", "severity": "critical", "element": {"selector": "#b"}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_same_element_same_rule_deduped_higher_severity_wins():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#x"}},
        {"rule": "color-contrast", "severity": "critical", "element": {"selector": "#x"}},
    ]
    deduped = deduplicate_issues(issues)
    assert len(deduped) == 1
    assert deduped[0]["severity"] == "critical"


def test_different_rules_on_same_element_not_merged():
    """Distinct rules on the same element stay separate — they describe
    different problems even if they share a prefix."""
    issues = [
        {"rule": "forms-input-no-label", "severity": "critical", "element": {"selector": "#x"}},
        {
            "rule": "forms-aria-invalid-no-description",
            "severity": "moderate",
            "element": {"selector": "#x"},
        },
    ]
    deduped = deduplicate_issues(issues)
    assert len(deduped) == 2


def test_different_selectors_not_merged():
    issues = [
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#x"}},
        {"rule": "color-contrast", "severity": "serious", "element": {"selector": "#y"}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_page_level_rules_with_empty_selector_stay_separate():
    issues = [
        {"rule": "structure-no-h1", "severity": "moderate", "element": {"selector": ""}},
        {"rule": "structure-no-main", "severity": "moderate", "element": {"selector": ""}},
    ]
    assert len(deduplicate_issues(issues)) == 2


def test_preserves_first_seen_order():
    issues = [
        {"rule": "r-one", "severity": "minor", "element": {"selector": "#a"}},
        {"rule": "s-one", "severity": "minor", "element": {"selector": "#b"}},
        {"rule": "r-one", "severity": "minor", "element": {"selector": "#a"}},  # dup of first
    ]
    deduped = deduplicate_issues(issues)
    assert [i["element"]["selector"] for i in deduped] == ["#a", "#b"]


def test_cross_module_alias_collapses_axe_plus_our_rule():
    """axe 'image-alt' + our 'media-img-no-alt' on the same element
    should collapse to the custom rule. The axe finding's wcag
    criteria and evidence are folded into the kept issue."""
    axe = {
        "id": "axe-image-alt-0",
        "rule": "image-alt",
        "severity": "critical",
        "evidence": ["axe"],
        "wcag_criteria": ["1.1.1"],
        "element": {"selector": "img.logo", "html_snippet": "<img class='logo'>"},
    }
    ours = make_issue(
        issue_id="media-1",
        module="media",
        rule="media-img-no-alt",
        severity="critical",
        wcag=["1.1.1"],
        title="Missing alt",
        selector="img.logo",
        html_snippet="<img class='logo'>",
    )
    deduped = deduplicate_issues([axe, ours])
    assert len(deduped) == 1
    kept = deduped[0]
    assert kept["rule"] == "media-img-no-alt"
    # Evidence now reflects both detectors.
    assert "axe" in kept.get("evidence", [])
    assert "media" in kept.get("evidence", [])


def test_cross_module_alias_keeps_axe_when_ours_not_present():
    """If only the axe variant fires, it's unique coverage — keep it."""
    axe_only = {
        "id": "axe-image-alt-0",
        "rule": "image-alt",
        "severity": "critical",
        "evidence": ["axe"],
        "element": {"selector": "img.banner"},
    }
    deduped = deduplicate_issues([axe_only])
    assert len(deduped) == 1
    assert deduped[0]["rule"] == "image-alt"


def test_cross_module_alias_resilient_to_different_selectors():
    """Two modules synthesize different selectors for the same DOM node
    (axe gets '#a > img', we get 'img.logo'). The fingerprint-based
    alias check should still collapse them — as long as the html
    snippets match."""
    axe = {
        "rule": "image-alt",
        "severity": "critical",
        "evidence": ["axe"],
        "element": {
            "selector": "#a > img",
            "html_snippet": "<img alt='' class='logo'>",
        },
    }
    ours = make_issue(
        issue_id="m", module="media", rule="media-img-no-alt",
        severity="critical", wcag=["1.1.1"], title="",
        selector="img.logo",
        html_snippet="<img alt='' class='logo'>",
    )
    deduped = deduplicate_issues([axe, ours])
    assert len(deduped) == 1


def test_same_rule_same_element_evidence_is_unioned():
    """Two axe passes on the same (rule, element) — evidence from the
    dropped one should not be silently lost."""
    a = {
        "rule": "color-contrast", "severity": "serious",
        "evidence": ["axe-pass-1"],
        "element": {"selector": "#x", "html_snippet": "<x/>"},
    }
    b = {
        "rule": "color-contrast", "severity": "serious",
        "evidence": ["axe-pass-2"],
        "element": {"selector": "#x", "html_snippet": "<x/>"},
    }
    deduped = deduplicate_issues([a, b])
    assert len(deduped) == 1
    ev = deduped[0].get("evidence") or []
    assert "axe-pass-1" in ev
    assert "axe-pass-2" in ev


def test_validate_cross_module_aliases_matches_real_rule_set():
    """CI-invariant: every canonical rule in the alias table must be
    a rule id some custom module still emits. Keeps the table from
    drifting silently after a rename."""
    # Hand-curated from the actual rule emissions. Sourced from
    # `grep -rn 'rule="' audit/*.py` — keep this list in sync with
    # make_issue call sites whenever new rules are added.
    known = {
        # aria
        "aria-invalid-role", "aria-labelledby-missing",
        "aria-describedby-missing", "aria-hidden-focusable",
        # media
        "media-img-no-alt", "media-img-placeholder-alt",
        # forms
        "forms-input-no-label",
        # responsive
        "responsive-target-size", "responsive-viewport-zoom-disabled",
        # screen-reader (Path A + Path B)
        "sr-silent-interactive", "sr-empty-heading", "sr-dialog-no-name",
        "sr-label-in-name", "sr-nvda-silent", "sr-nvda-mismatch",
        # structure
        "structure-heading-skip", "structure-html-lang",
        "structure-title-missing",
        # cognitive (reading-level added by VLM pivot)
        "cognitive-reading-level-high",
        # vlm — LLM-judged semantic checks
        "vlm-alt-unhelpful", "vlm-visual-heading-missing",
        "vlm-heading-hierarchy-mismatch", "vlm-link-ambiguous",
        "vlm-error-unclear",
    }
    errs = validate_cross_module_aliases(known)
    assert errs == [], errs


def test_validate_cross_module_aliases_flags_missing_rule():
    """If an alias points at a rule not in the known set, the
    validator must flag it — this is the drift-detection machinery."""
    # Deliberately incomplete set.
    partial = {"aria-hidden-focusable"}
    errs = validate_cross_module_aliases(partial)
    # Many aliases map to rules outside the partial set; at least one
    # error is expected, and mentions the missing rule.
    assert errs
    assert any("media-img-no-alt" in e for e in errs)


def test_path_a_and_path_b_reconciled_on_same_element():
    """When both Path A (sr-silent-interactive) and Path B (sr-nvda-silent)
    flag the same element, the NVDA finding wins and the a11y-tree
    observation becomes evidence."""
    path_a = make_issue(
        issue_id="a", module="screen_reader",
        rule="sr-silent-interactive", severity="critical",
        wcag=["4.1.2"], title="",
        selector="button.go", html_snippet="<button class='go'></button>",
    )
    path_b = make_issue(
        issue_id="b", module="screen_reader",
        rule="sr-nvda-silent", severity="critical",
        wcag=["4.1.2"], title="",
        selector="button.go", html_snippet="<button class='go'></button>",
    )
    deduped = deduplicate_issues([path_a, path_b])
    assert len(deduped) == 1
    kept = deduped[0]
    assert kept["rule"] == "sr-nvda-silent"
    # Both detection sources retained as evidence.
    ev = kept.get("evidence") or []
    assert "screen_reader" in ev


def test_cross_module_aliases_is_not_empty():
    """Sanity: the table shouldn't be accidentally cleared."""
    assert CROSS_MODULE_ALIASES
    assert "image-alt" in CROSS_MODULE_ALIASES
