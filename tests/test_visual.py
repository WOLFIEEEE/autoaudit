from audit.visual import analyze


def _rules(issues):
    return [i["rule"] for i in issues]


def test_clean_page_has_no_issues():
    dom = {"marquee": [], "infinite_animations": [], "tiny_text": []}
    assert analyze(dom) == []


def test_marquee_flagged():
    dom = {
        "marquee": [{"tag": "marquee", "selector": "marquee", "html": "<marquee>"}],
        "infinite_animations": [],
        "tiny_text": [],
    }
    issues = analyze(dom)
    assert _rules(issues) == ["visual-marquee-or-blink"]
    assert issues[0]["severity"] == "serious"


def test_blink_flagged():
    dom = {
        "marquee": [{"tag": "blink", "selector": "blink", "html": "<blink>"}],
        "infinite_animations": [],
        "tiny_text": [],
    }
    assert _rules(analyze(dom)) == ["visual-marquee-or-blink"]


def test_long_infinite_animation_flagged():
    dom = {
        "marquee": [],
        "infinite_animations": [
            {
                "tag": "div",
                "selector": "div#banner",
                "html": "<div>",
                "animation_name": "slide",
                "duration_s": 4.0,
            }
        ],
        "tiny_text": [],
    }
    issues = analyze(dom)
    assert _rules(issues) == ["visual-infinite-animation"]
    assert issues[0]["details"]["duration_s"] == 4.0


def test_very_short_animation_ignored():
    # Micro-interactions (hover pulse, 0.3s fade) shouldn't trip 2.2.2.
    dom = {
        "marquee": [],
        "infinite_animations": [
            {
                "tag": "span",
                "selector": "span",
                "html": "<span>",
                "animation_name": "pulse",
                "duration_s": 0.3,
            }
        ],
        "tiny_text": [],
    }
    assert analyze(dom) == []


def test_tiny_text_flagged():
    dom = {
        "marquee": [],
        "infinite_animations": [],
        "tiny_text": [
            {"selector": "p.note", "html": "<p>", "font_size_px": 7.5, "tag": "p"}
        ],
    }
    issues = analyze(dom)
    assert _rules(issues) == ["visual-tiny-text"]
    assert issues[0]["details"]["font_size_px"] == 7.5
    assert issues[0]["severity"] == "minor"


def test_multiple_rules_fire_together():
    dom = {
        "marquee": [{"tag": "marquee", "selector": "marquee", "html": "<m>"}],
        "infinite_animations": [
            {
                "tag": "div",
                "selector": "div",
                "html": "<div>",
                "animation_name": "spin",
                "duration_s": 2.0,
            }
        ],
        "tiny_text": [
            {"selector": "small", "html": "<small>", "font_size_px": 6.0, "tag": "small"}
        ],
    }
    rules = set(_rules(analyze(dom)))
    assert rules == {
        "visual-marquee-or-blink",
        "visual-infinite-animation",
        "visual-tiny-text",
    }
