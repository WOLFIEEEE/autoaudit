from audit.cognitive import analyze


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
