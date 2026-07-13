"""Bundled tests for the modules added in the latest enhancement pass.

One test file per module would be cleaner long-term; bundling here
because each module's surface is small and the cross-module shared
fixture (a `_FakePage`) would otherwise be duplicated.
"""

from __future__ import annotations

from typing import Any

from audit import (
    accessible_auth,
    auto_fix,
    dragging,
    focus_obscured,
    lang_detection,
    redundant_entry,
)


class _FakePage:
    def __init__(self, payload: Any):
        self._payload = payload

    def evaluate(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        return self._payload


# ---------------------------------------------------------------------
# audit/dragging.py
# ---------------------------------------------------------------------


def test_dragging_handler_with_no_alternative_fires():
    probe = {
        "draggables": [{
            "tag": "li",
            "selector": "li.sortable",
            "html": "<li class=sortable></li>",
            "class_signal": True,
            "attr_signal": False,
            "has_sibling_alternative": False,
        }],
        "sliders": [],
    }
    issues = dragging.analyze(probe)
    assert any(i["rule"] == "dragging-handler-on-element" for i in issues)


def test_dragging_handler_with_alternative_does_not_fire():
    probe = {
        "draggables": [{
            "tag": "li",
            "selector": "li.sortable",
            "html": "<li class=sortable></li>",
            "class_signal": True,
            "attr_signal": False,
            "has_sibling_alternative": True,  # "Move up" button nearby
        }],
        "sliders": [],
    }
    assert dragging.analyze(probe) == []


def test_dragging_slider_without_keyboard_fires():
    probe = {
        "draggables": [],
        "sliders": [{
            "selector": "[role=slider]",
            "html": "<div role=slider></div>",
            "has_keydown_attr": False,
            "tabbable": False,
        }],
    }
    issues = dragging.analyze(probe)
    assert any(i["rule"] == "dragging-no-keyboard-alt" for i in issues)


def test_dragging_slider_with_keyboard_does_not_fire():
    probe = {
        "draggables": [],
        "sliders": [{
            "selector": "[role=slider]",
            "html": "<div role=slider tabindex=0></div>",
            "has_keydown_attr": False,
            "tabbable": True,
        }],
    }
    assert dragging.analyze(probe) == []


# ---------------------------------------------------------------------
# audit/redundant_entry.py
# ---------------------------------------------------------------------


def test_redundant_same_name_across_forms_no_autocomplete_fires():
    probe = {
        "forms": [
            {"form_idx": 0, "fields": [
                {"name": "email", "type": "email", "autocomplete": "", "selector": "input"},
            ]},
            {"form_idx": 1, "fields": [
                {"name": "email", "type": "email", "autocomplete": "", "selector": "input"},
            ]},
        ],
        "has_wizard_signal": True,
    }
    issues = redundant_entry.analyze(probe)
    assert any(i["rule"] == "redundant-entry-no-autocomplete" for i in issues)


def test_redundant_with_autocomplete_does_not_fire():
    probe = {
        "forms": [
            {"form_idx": 0, "fields": [
                {"name": "email", "autocomplete": "email", "type": "email", "selector": "input"},
            ]},
            {"form_idx": 1, "fields": [
                {"name": "email", "autocomplete": "", "type": "email", "selector": "input"},
            ]},
        ],
    }
    assert redundant_entry.analyze(probe) == []


def test_redundant_benign_names_do_not_fire():
    probe = {
        "forms": [
            {"form_idx": 0, "fields": [
                {"name": "q", "type": "search", "autocomplete": "", "selector": "input"},
            ]},
            {"form_idx": 1, "fields": [
                {"name": "q", "type": "search", "autocomplete": "", "selector": "input"},
            ]},
        ],
    }
    assert redundant_entry.analyze(probe) == []


# ---------------------------------------------------------------------
# audit/accessible_auth.py
# ---------------------------------------------------------------------


def test_accessible_auth_captcha_on_password_page_fires():
    probe = {
        "auth_context": True,
        "captcha_findings": [
            {"vendor": "reCAPTCHA", "marker": "g-recaptcha",
             "selector": ".g-recaptcha", "html": "<div class=g-recaptcha />"},
        ],
        "auth_prompts": [],
    }
    issues = accessible_auth.analyze(probe)
    assert any(i["rule"] == "accessible-auth-captcha-detected" for i in issues)


def test_accessible_auth_captcha_outside_auth_does_not_fire():
    probe = {
        "auth_context": False,  # no password field
        "captcha_findings": [
            {"vendor": "hCaptcha", "marker": "h-captcha",
             "selector": ".h-captcha", "html": ""},
        ],
        "auth_prompts": [],
    }
    assert accessible_auth.analyze(probe) == []


def test_accessible_auth_cognitive_prompt_fires():
    probe = {
        "auth_context": True,
        "captcha_findings": [],
        "auth_prompts": [
            {"text": "Type the letters you see above",
             "selector": "label", "html": "<label>...</label>"},
        ],
    }
    issues = accessible_auth.analyze(probe)
    assert any(i["rule"] == "accessible-auth-cognitive-test" for i in issues)


# ---------------------------------------------------------------------
# audit/focus_obscured.py
# ---------------------------------------------------------------------


def test_focus_obscured_when_overlay_covers_stop():
    stop = {
        "selector": "#real-button",
        "bbox": {"x": 100, "y": 100, "w": 80, "h": 30},
        "html_snippet": "<button id=real-button>Buy</button>",
    }
    overlay = {
        "selector": "#sticky-header",
        "position": "sticky",
        "x": 0, "y": 0, "w": 1000, "h": 200,
        "z_index": "10",
        "html": "<header id=sticky-header />",
    }
    issues = focus_obscured.analyze([stop], [overlay])
    assert any(i["rule"] == "focus-obscured-by-sticky" for i in issues)


def test_focus_not_obscured_when_overlay_misses():
    stop = {
        "selector": "#real-button",
        "bbox": {"x": 100, "y": 500, "w": 80, "h": 30},
    }
    overlay = {
        "selector": "#sticky-header",
        "position": "sticky",
        "x": 0, "y": 0, "w": 1000, "h": 80,
        "z_index": "10",
    }
    assert focus_obscured.analyze([stop], [overlay]) == []


def test_focus_not_obscured_when_focus_is_inside_overlay():
    """If focus is on a descendant of the overlay (e.g. a button
    inside a modal), 2.4.11 doesn't apply."""
    stop = {
        "selector": "#modal > button",
        "bbox": {"x": 100, "y": 100, "w": 80, "h": 30},
    }
    overlay = {
        "selector": "#modal",
        "position": "fixed",
        "x": 0, "y": 0, "w": 1000, "h": 1000,
    }
    assert focus_obscured.analyze([stop], [overlay]) == []


# ---------------------------------------------------------------------
# audit/lang_detection.py
# ---------------------------------------------------------------------


def test_lang_detection_english_with_english_text_does_not_fire():
    probe = {"lang": "en", "text": "Hello world. " * 20}
    assert lang_detection.analyze(probe) == []


def test_lang_detection_english_declared_with_cyrillic_text_fires():
    probe = {
        "lang": "en",
        "text": "Привет мир. Это страница для тестирования. " * 5,
    }
    issues = lang_detection.analyze(probe)
    assert any(i["rule"] == "structure-lang-content-mismatch" for i in issues)
    issue = issues[0]
    assert issue["details"]["dominant_script"] == "Cyrillic"
    assert issue["details"]["expected_script"] == "Latin"


def test_lang_detection_too_short_text_bails():
    probe = {"lang": "en", "text": "Привет"}
    assert lang_detection.analyze(probe) == []


def test_lang_detection_unknown_lang_bails():
    probe = {"lang": "xx-YZ", "text": "Some text " * 30}
    assert lang_detection.analyze(probe) == []


# ---------------------------------------------------------------------
# audit/auto_fix.py
# ---------------------------------------------------------------------


def test_auto_fix_html_lang_emits_patch():
    audit = {"issues": [{
        "id": "x", "rule": "structure-html-lang", "fingerprint": "abc",
        "element": {"selector": "html", "html_snippet": "<html>"},
    }]}
    patches = auto_fix.generate_patches(audit)
    assert len(patches) == 1
    assert 'lang="en"' in patches[0]["after"]


def test_auto_fix_aria_hidden_focusable_emits_remove():
    audit = {"issues": [{
        "id": "x", "rule": "aria-hidden-focusable", "fingerprint": "abc",
        "element": {
            "selector": "button",
            "html_snippet": '<button aria-hidden="true" tabindex="0">Buy</button>',
        },
    }]}
    patches = auto_fix.generate_patches(audit)
    assert len(patches) == 1
    assert "aria-hidden" not in patches[0]["after"]


def test_auto_fix_positive_tabindex_replaces():
    audit = {"issues": [{
        "id": "x", "rule": "keyboard-positive-tabindex", "fingerprint": "abc",
        "element": {
            "selector": "button",
            "html_snippet": '<button tabindex="5">A</button>',
        },
    }]}
    patches = auto_fix.generate_patches(audit)
    assert len(patches) == 1
    assert 'tabindex="0"' in patches[0]["after"]


def test_auto_fix_unknown_rule_returns_no_patch():
    audit = {"issues": [{
        "id": "x", "rule": "color-only-link", "fingerprint": "abc",
        "element": {"selector": "a", "html_snippet": "<a>x</a>"},
    }]}
    assert auto_fix.generate_patches(audit) == []


# ---------------------------------------------------------------------
# audit/sitemap.py - smart template sampling
# ---------------------------------------------------------------------


def test_url_template_normalises_numeric_and_uuid_segments():
    from audit import sitemap
    assert sitemap.url_template("https://x.example/products/12345") == "/products/{n}"
    assert sitemap.url_template(
        "https://x.example/orders/8b3a1d4e-1234-4321-aaaa-bbbbccccdddd"
    ) == "/orders/{uuid}"
    assert (
        sitemap.url_template("https://x.example/blog/some-long-slug-name-here")
        == "/blog/{slug}"
    )


def test_sample_by_template_picks_one_per_template():
    from audit import sitemap
    urls = [
        "https://x/products/1", "https://x/products/2", "https://x/products/3",
        "https://x/blog/post-name-one", "https://x/blog/another-slug-name",
        "https://x/about",
    ]
    picked = sitemap.sample_by_template(urls, n=10, root_url="https://x")
    # Three template buckets: /products/{n}, /blog/{slug}, /about
    assert len(picked) == 3
