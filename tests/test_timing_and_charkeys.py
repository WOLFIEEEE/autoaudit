"""Unit tests for the two manual_only -> partial conversions:

  - audit/char_key_shortcuts.py  (WCAG 2.1.4)
  - audit/timing.py              (WCAG 2.2.1)

Both modules expose a pure `analyze(probe)` over a JS-extracted
snapshot, so these tests need no browser. `run()` is exercised through a
tiny fake page to confirm the probe->analyze wiring and the fail-closed
error path.
"""

from __future__ import annotations

from typing import Any

from audit import char_key_shortcuts, timing


class _FakePage:
    def __init__(self, payload: Any, *, raise_exc: bool = False):
        self._payload = payload
        self._raise = raise_exc

    def evaluate(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        if self._raise:
            raise RuntimeError("boom")
        return self._payload


# ---------------------------------------------------------------------
# audit/char_key_shortcuts.py  — WCAG 2.1.4
# ---------------------------------------------------------------------


def test_single_char_accesskey_fires():
    probe = {
        "accesskeys": [
            {"accesskey": "s", "tag": "button",
             "selector": "button#search", "html": "<button accesskey=s>"}
        ],
        "inline_key_handlers": [],
    }
    issues = char_key_shortcuts.analyze(probe)
    assert any(i["rule"] == "char-key-shortcut-accesskey" for i in issues)
    iss = next(i for i in issues if i["rule"] == "char-key-shortcut-accesskey")
    assert iss["wcag_criteria"] == ["2.1.4"]
    assert iss["confidence"] == "medium"


def test_multi_char_accesskey_token_does_not_fire():
    # accesskey values are space-separated candidates; a single 1-char
    # candidate triggers. A purely multi-char value must not.
    probe = {
        "accesskeys": [
            {"accesskey": "Enter", "tag": "button",
             "selector": "button", "html": "<button accesskey=Enter>"}
        ],
        "inline_key_handlers": [],
    }
    assert char_key_shortcuts.analyze(probe) == []


def test_unguarded_single_key_handler_fires():
    probe = {
        "accesskeys": [],
        "inline_key_handlers": [
            {"target": "body", "attribute": "onkeydown",
             "source": "if (event.key === 's') openSearch()",
             "selector": "body", "html": "<body onkeydown=...>"}
        ],
    }
    issues = char_key_shortcuts.analyze(probe)
    assert any(i["rule"] == "char-key-shortcut-single-key-handler" for i in issues)


def test_modifier_guarded_handler_does_not_fire():
    # Ctrl/Alt/Meta guard means it's not a bare character shortcut.
    probe = {
        "accesskeys": [],
        "inline_key_handlers": [
            {"target": "body", "attribute": "onkeydown",
             "source": "if (event.ctrlKey && event.key === 's') save()",
             "selector": "body", "html": "<body onkeydown=...>"}
        ],
    }
    assert char_key_shortcuts.analyze(probe) == []


def test_handler_without_single_key_compare_does_not_fire():
    # Reading the key for logging, no single-char comparison.
    probe = {
        "accesskeys": [],
        "inline_key_handlers": [
            {"target": "div", "attribute": "onkeyup",
             "source": "log(event.key)",
             "selector": "div", "html": "<div onkeyup=...>"}
        ],
    }
    assert char_key_shortcuts.analyze(probe) == []


def test_charkeys_run_wraps_probe():
    page = _FakePage({
        "accesskeys": [{"accesskey": "/", "tag": "input",
                        "selector": "input", "html": "<input accesskey=/>"}],
        "inline_key_handlers": [],
    })
    out = char_key_shortcuts.run(page, {})
    assert out["ran"] is True
    assert out["accesskey_candidates"] == 1
    assert len(out["issues"]) == 1


def test_charkeys_run_fails_closed_on_probe_error():
    page = _FakePage(None, raise_exc=True)
    out = char_key_shortcuts.run(page, {})
    assert out["ran"] is False
    assert out["issues"] == []
    assert "error" in out


# ---------------------------------------------------------------------
# audit/timing.py  — WCAG 2.2.1
# ---------------------------------------------------------------------


def test_meta_refresh_reload_short_delay_is_serious():
    probe = {"meta_refresh": [
        {"content": "5", "html": "<meta http-equiv=refresh content=5>",
         "selector": "meta[http-equiv='refresh']"}
    ]}
    issues = timing.analyze(probe)
    assert len(issues) == 1
    assert issues[0]["rule"] == "timing-meta-refresh"
    assert issues[0]["severity"] == "serious"
    assert issues[0]["wcag_criteria"] == ["2.2.1"]


def test_meta_refresh_long_delay_is_moderate():
    probe = {"meta_refresh": [
        {"content": "120", "html": "<meta http-equiv=refresh content=120>"}
    ]}
    issues = timing.analyze(probe)
    assert issues[0]["severity"] == "moderate"


def test_meta_refresh_timed_redirect_fires_redirect_rule():
    probe = {"meta_refresh": [
        {"content": "10; url=https://example.com/next",
         "html": "<meta http-equiv=refresh content='10; url=...'>"}
    ]}
    issues = timing.analyze(probe)
    assert issues[0]["rule"] == "timing-meta-refresh-redirect"
    assert issues[0]["details"]["redirect_url"] == "https://example.com/next"


def test_instant_redirect_flagged():
    probe = {"meta_refresh": [
        {"content": "0;url=/login", "html": "<meta http-equiv=refresh>"}
    ]}
    issues = timing.analyze(probe)
    assert issues[0]["rule"] == "timing-meta-refresh-redirect"
    assert issues[0]["details"]["instant"] is True
    assert issues[0]["severity"] == "serious"


def test_malformed_refresh_content_ignored():
    probe = {"meta_refresh": [
        {"content": "not-a-number", "html": "<meta>"},
        {"content": "", "html": "<meta>"},
    ]}
    assert timing.analyze(probe) == []


def test_timing_run_wraps_probe():
    page = _FakePage({"meta_refresh": [{"content": "3", "html": "<meta>"}]})
    out = timing.run(page, {})
    assert out["ran"] is True
    assert out["meta_refresh_candidates"] == 1
    assert len(out["issues"]) == 1


def test_timing_run_fails_closed_on_probe_error():
    page = _FakePage(None, raise_exc=True)
    out = timing.run(page, {})
    assert out["ran"] is False
    assert out["issues"] == []
