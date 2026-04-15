from audit.responsive import analyze, _zoom_disabled


def _rules(issues):
    return [i["rule"] for i in issues]


def _target(**overrides):
    base = {
        "tag": "button",
        "type": "",
        "role": "",
        "width": 48,
        "height": 48,
        "display": "inline-block",
        "visibility": "visible",
        "offscreen": False,
        "selector": "button",
        "html": "<button>ok</button>",
    }
    base.update(overrides)
    return base


def _dom(*, viewport=None, targets=None):
    return {
        "viewport": viewport if viewport is not None else {"present": True, "content": "width=device-width, initial-scale=1"},
        "targets": targets or [],
    }


def test_healthy_page_has_no_issues():
    assert analyze(_dom(targets=[_target()])) == []


def test_missing_viewport_meta_flagged():
    issues = analyze(_dom(viewport={"present": False, "content": ""}))
    assert _rules(issues) == ["responsive-viewport-meta-missing"]


def test_zoom_disabled_detection():
    assert _zoom_disabled("user-scalable=no")
    assert _zoom_disabled("user-scalable=0")
    assert _zoom_disabled("maximum-scale=1")
    assert _zoom_disabled("width=device-width, maximum-scale=1.0")
    assert not _zoom_disabled("width=device-width, initial-scale=1")
    assert not _zoom_disabled("maximum-scale=2")
    assert not _zoom_disabled("")


def test_zoom_disabled_flagged():
    issues = analyze(
        _dom(viewport={"present": True, "content": "width=device-width, user-scalable=no"})
    )
    assert _rules(issues) == ["responsive-viewport-zoom-disabled"]


def test_small_target_flagged():
    issues = analyze(_dom(targets=[_target(width=20, height=20)]))
    assert _rules(issues) == ["responsive-target-size"]
    assert issues[0]["details"]["width"] == 20


def test_inline_target_exempt():
    # Inline anchors inside flowing text are exempt per 2.5.8.
    dom = _dom(targets=[_target(tag="a", display="inline", width=10, height=10)])
    assert analyze(dom) == []


def test_offscreen_target_skipped():
    dom = _dom(targets=[_target(width=0, height=0, offscreen=True)])
    assert analyze(dom) == []


def test_one_dimension_too_small_flagged():
    # Both dimensions must be >= 24.
    dom = _dom(targets=[_target(width=30, height=20)])
    assert _rules(analyze(dom)) == ["responsive-target-size"]


def test_exactly_minimum_ok():
    dom = _dom(targets=[_target(width=24, height=24)])
    assert analyze(dom) == []
