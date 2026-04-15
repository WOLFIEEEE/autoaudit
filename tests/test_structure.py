from audit.structure import analyze


def _rules(issues):
    return {i["rule"] for i in issues}


def test_perfect_structure_has_no_issues():
    dom = {
        "lang": "en",
        "title": "My Page",
        "headings": [
            {"level": 1, "text": "Title", "selector": "h1", "html": "<h1>Title</h1>"},
            {"level": 2, "text": "Section", "selector": "h2", "html": "<h2>...</h2>"},
        ],
        "landmarks": {"main": 1, "nav": 1, "banner": 1, "contentinfo": 1},
        "tables": [],
    }
    assert analyze(dom) == []


def test_missing_lang_title_main_and_h1():
    dom = {
        "lang": "",
        "title": "",
        "headings": [],
        "landmarks": {"main": 0},
        "tables": [],
    }
    rules = _rules(analyze(dom))
    assert "structure-html-lang" in rules
    assert "structure-title-missing" in rules
    assert "structure-no-h1" in rules
    assert "structure-no-main" in rules


def test_multiple_h1():
    dom = {
        "lang": "en",
        "title": "x",
        "headings": [
            {"level": 1, "text": "a", "selector": "h1", "html": "<h1>a</h1>"},
            {"level": 1, "text": "b", "selector": "h1:nth-of-type(2)", "html": "<h1>b</h1>"},
        ],
        "landmarks": {"main": 1},
        "tables": [],
    }
    rules = _rules(analyze(dom))
    assert "structure-multiple-h1" in rules
    # Zero-h1 rule should NOT fire here.
    assert "structure-no-h1" not in rules


def test_heading_skip_detected():
    dom = {
        "lang": "en",
        "title": "x",
        "headings": [
            {"level": 1, "text": "top", "selector": "h1", "html": "<h1>top</h1>"},
            {"level": 3, "text": "deep", "selector": "h3", "html": "<h3>deep</h3>"},
        ],
        "landmarks": {"main": 1},
        "tables": [],
    }
    issues = analyze(dom)
    skip = [i for i in issues if i["rule"] == "structure-heading-skip"]
    assert len(skip) == 1
    assert skip[0]["details"]["from_level"] == 1
    assert skip[0]["details"]["to_level"] == 3


def test_table_without_th_is_flagged():
    dom = {
        "lang": "en",
        "title": "x",
        "headings": [{"level": 1, "text": "x", "selector": "h1", "html": "<h1>x</h1>"}],
        "landmarks": {"main": 1},
        "tables": [
            {"has_th": False, "has_caption": False, "selector": "table", "html": "<table>..."},
            {"has_th": True, "has_caption": True, "selector": "table:nth-of-type(2)", "html": "<table>..."},
        ],
    }
    issues = analyze(dom)
    th_issues = [i for i in issues if i["rule"] == "structure-table-no-th"]
    assert len(th_issues) == 1
