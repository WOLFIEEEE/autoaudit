from audit.cognitive import (
    _flesch_kincaid_grade,
    analyze,
    analyze_reading_level,
)


def _rules(issues):
    return [i["rule"] for i in issues]


def test_good_links_produce_no_issues():
    links = [
        {"text": "Read the 2025 annual report", "href": "/report-2025", "selector": "a", "html": "<a>"},
        {"text": "Contact support", "href": "/support", "selector": "a:nth-of-type(2)", "html": "<a>"},
    ]
    assert analyze(links) == []


def test_empty_link_text_is_serious():
    links = [{"text": "", "href": "/x", "selector": "a", "html": "<a>"}]
    issues = analyze(links)
    assert _rules(issues) == ["cognitive-empty-link"]
    assert issues[0]["severity"] == "serious"


def test_generic_phrases_flagged():
    for phrase in ["click here", "Read More", "HERE", "more", "learn more"]:
        links = [{"text": phrase, "href": "/x", "selector": "a", "html": "<a>"}]
        assert _rules(analyze(links)) == ["cognitive-generic-link-text"]


def test_duplicate_text_different_urls_flagged():
    links = [
        {"text": "Documentation", "href": "/docs/v1", "selector": "a", "html": "<a>"},
        {"text": "Documentation", "href": "/docs/v2", "selector": "a:nth-of-type(2)", "html": "<a>"},
    ]
    issues = analyze(links)
    assert "cognitive-duplicate-link-text" in _rules(issues)
    # Only the second occurrence is reported.
    dup = [i for i in issues if i["rule"] == "cognitive-duplicate-link-text"]
    assert len(dup) == 1
    assert dup[0]["details"]["distinct_urls"] == ["/docs/v1", "/docs/v2"]


def test_duplicate_text_same_url_not_flagged():
    links = [
        {"text": "Home", "href": "/", "selector": "a", "html": "<a>"},
        {"text": "Home", "href": "/", "selector": "a:nth-of-type(2)", "html": "<a>"},
    ]
    assert "cognitive-duplicate-link-text" not in _rules(analyze(links))


# ---------------------------------------------------------------------
# Reading level (WCAG 3.1.5). Pure text-statistical, no network.


def test_reading_level_short_text_returns_none():
    # Under MIN_WORDS_FOR_READING_ANALYSIS = 50. Don't flag.
    assert _flesch_kincaid_grade("Hello world. Short.") is None


def test_reading_level_simple_text_not_flagged():
    # Clear, short-sentence content should land well below the flag
    # threshold (grade 10). Generated to be >= 50 words.
    text = " ".join([
        "We sell cats.",
        "The cats are fluffy.",
        "They eat fish and sleep all day.",
        "You can adopt one at our shop.",
        "We are open every day except Sunday.",
        "Bring a bag and a toy.",
        "We will meet you at the door.",
        "Your new cat will be happy with you.",
        "Say hello and come by soon.",
        "Kids can pet the cats too.",
    ])
    issues = analyze_reading_level(text)
    assert issues == []


def test_reading_level_flags_grad_school_prose():
    # Long sentences, polysyllabic vocabulary, passive constructions —
    # pushes Flesch-Kincaid grade well above 10.
    text = (
        "The incontrovertible epistemological implications of multivariate "
        "regression analysis, when juxtaposed with the phenomenological "
        "underpinnings of observational methodologies, necessitate a "
        "comprehensive reconceptualization of the theoretical frameworks "
        "traditionally employed within the discipline, particularly insofar "
        "as such frameworks presuppose an ontological stability that is "
        "rendered untenable by contemporary critiques of post-positivist "
        "approaches to empirical inquiry, which collectively demand a "
        "thoroughgoing methodological reappraisal capable of accommodating "
        "the inherent indeterminacies of social-scientific investigation."
    )
    issues = analyze_reading_level(text)
    assert _rules(issues) == ["cognitive-reading-level-high"]
    issue = issues[0]
    assert issue["severity"] == "minor"
    assert issue["wcag_criteria"] == ["3.1.5"]
    assert issue["details"]["flesch_kincaid_grade"] > 10
    assert issue["confidence"] == "medium"


def test_reading_level_issue_carries_level_aaa():
    """3.1.5 is AAA — make_issue should derive level=AAA from the SC."""
    text = " ".join([
        "The incontrovertible epistemological implications of multivariate "
        "regression analysis necessitate reconceptualization of foundational "
        "frameworks employed across interdisciplinary investigative paradigms, "
        "particularly insofar as such frameworks presuppose ontological "
        "stability that contemporary post-positivist critiques render untenable."
    ] * 2)
    issues = analyze_reading_level(text)
    assert issues and issues[0]["level"] == "AAA"
