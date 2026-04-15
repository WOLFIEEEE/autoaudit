from audit.media import analyze, _looks_like_placeholder


def _rules(issues):
    return [i["rule"] for i in issues]


def test_placeholder_detection_catches_filenames():
    assert _looks_like_placeholder("photo.jpg")
    assert _looks_like_placeholder("IMG_1234.png")
    assert _looks_like_placeholder("image")
    assert _looks_like_placeholder("icon")
    assert not _looks_like_placeholder("A woman reading a book in a library")


def test_missing_alt_is_critical():
    dom = {
        "images": [
            {
                "alt": None,
                "src": "banner.png",
                "role": None,
                "aria_hidden": False,
                "selector": "img",
                "html": "<img>",
            }
        ],
        "videos": [],
        "audios": [],
    }
    issues = analyze(dom)
    assert len(issues) == 1
    assert issues[0]["rule"] == "media-img-no-alt"
    assert issues[0]["severity"] == "critical"


def test_empty_alt_with_role_presentation_is_ok():
    dom = {
        "images": [
            {
                "alt": "",
                "src": "decor.svg",
                "role": "presentation",
                "aria_hidden": False,
                "selector": "img",
                "html": "<img>",
            }
        ],
        "videos": [],
        "audios": [],
    }
    assert analyze(dom) == []


def test_decorative_image_with_alt_text_is_flagged():
    dom = {
        "images": [
            {
                "alt": "Decorative flourish",
                "src": "flourish.svg",
                "role": "presentation",
                "aria_hidden": False,
                "selector": "img",
                "html": "<img>",
            }
        ],
        "videos": [],
        "audios": [],
    }
    assert _rules(analyze(dom)) == ["media-img-decorative-text"]


def test_placeholder_alt_flagged():
    dom = {
        "images": [
            {
                "alt": "IMG_0042.jpg",
                "src": "IMG_0042.jpg",
                "role": None,
                "aria_hidden": False,
                "selector": "img",
                "html": "<img>",
            }
        ],
        "videos": [],
        "audios": [],
    }
    issues = analyze(dom)
    assert _rules(issues) == ["media-img-placeholder-alt"]
    assert issues[0]["severity"] == "moderate"


def test_video_without_caption_track():
    dom = {
        "images": [],
        "videos": [
            {
                "has_caption_track": False,
                "autoplay": False,
                "muted": False,
                "selector": "video",
                "html": "<video>",
            }
        ],
        "audios": [],
    }
    assert "media-video-no-track" in _rules(analyze(dom))


def test_autoplay_video_with_audio_flagged():
    dom = {
        "images": [],
        "videos": [
            {
                "has_caption_track": True,
                "autoplay": True,
                "muted": False,
                "selector": "video",
                "html": "<video>",
            }
        ],
        "audios": [],
    }
    assert "media-autoplay" in _rules(analyze(dom))


def test_muted_autoplay_video_allowed():
    dom = {
        "images": [],
        "videos": [
            {
                "has_caption_track": True,
                "autoplay": True,
                "muted": True,
                "selector": "video",
                "html": "<video>",
            }
        ],
        "audios": [],
    }
    # Muted autoplay is permitted under 1.4.2.
    assert "media-autoplay" not in _rules(analyze(dom))
