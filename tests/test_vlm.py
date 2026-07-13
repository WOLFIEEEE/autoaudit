"""Tests for audit.vlm — VLM-judged semantic checks.

The real module makes OpenRouter calls; these tests swap the caller for
a stub via `vlm.set_vlm_caller()`. The fake `Page` satisfies the two
Playwright APIs vlm.py uses: `.evaluate(js, *args)` and `.screenshot(...)`.
"""

from __future__ import annotations

from typing import Any

import pytest

from audit import vlm


# ---------------------------------------------------------------------
# Fake page + helpers.


class FakePage:
    """Minimal stand-in for a Playwright page.

    - `.evaluate(js)` returns whatever the test registered for that JS
      blob (keyed by substring to keep the test setup readable).
    - `.evaluate(js, sel)` returns the per-selector rect.
    - `.screenshot(clip=..., ...)` returns deterministic bytes.
    """

    def __init__(
        self,
        *,
        alt_images: list[dict[str, Any]] | None = None,
        headings: dict[str, Any] | None = None,
        links: list[dict[str, Any]] | None = None,
        errors: list[dict[str, Any]] | None = None,
        rect_by_selector: dict[str, dict[str, Any]] | None = None,
    ):
        self.alt_images = alt_images or []
        self.headings = headings or {"headings": [], "candidates": []}
        self.links = links or []
        self.errors = errors or []
        self.rect_by_selector = rect_by_selector or {}

    def evaluate(self, js: str, *args):
        # Dispatch by a substring unique to each JS blob. Keeps the test
        # setup from depending on whitespace equivalence.
        if args:
            # _capture_element_png calls with (sel) → rect.
            sel = args[0]
            return self.rect_by_selector.get(sel)
        if "querySelectorAll('img[alt]')" in js:
            return self.alt_images
        if "visual candidates" in js.lower() or "candidates.push" in js:
            return self.headings
        if "a[href]" in js and "aria-label" in js and "parent_text" in js:
            return self.links
        if "aria-live" in js or "invalid-feedback" in js:
            return self.errors
        if "collectText" in js:
            return ""
        return []

    def screenshot(self, **_kwargs) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _reset_caller():
    """Every test starts with no stub; reset after."""
    vlm.set_vlm_caller(None)
    yield
    vlm.set_vlm_caller(None)


def _call_with_stub(stub_fn):
    """Install the stub and return an options dict that opts in without
    requiring a real API key (the stub replaces the network call)."""
    vlm.set_vlm_caller(stub_fn)
    return {"vlm_checks": True, "vlm_model": "stub-model"}


# ---------------------------------------------------------------------
# Skip-path tests — module must fail closed in the default config.


def test_opt_out_returns_skipped_by_default():
    page = FakePage()
    result = vlm.run(page, options={})
    assert result["ran"] is False
    assert result["skipped"] is True
    assert result["issues"] == []
    assert "not enabled" in result["reason"]


def test_opted_in_but_no_api_key_and_no_stub_returns_skipped(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    vlm.set_vlm_caller(None)
    page = FakePage()
    result = vlm.run(page, options={"vlm_checks": True})
    assert result["ran"] is False
    assert "OPENROUTER_API_KEY" in result["reason"]


# ---------------------------------------------------------------------
# Alt-text check.


def test_alt_text_helpful_does_not_produce_issue():
    page = FakePage(
        alt_images=[{
            "alt": "A golden retriever catching a frisbee mid-air",
            "src": "/dog.jpg", "selector": "#dog", "area": 60000,
            "html": "<img id='dog'>",
        }],
        rect_by_selector={"#dog": {
            "x": 0, "y": 0, "w": 100, "h": 100, "vw": 1024, "vh": 768,
        }},
    )

    def stub(_messages, *, api_key, model):
        return {"helpful": True, "reason": "accurate", "better_alt": ""}

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    assert result["ran"] is True
    rules = [i["rule"] for i in result["issues"]]
    assert "vlm-alt-unhelpful" not in rules


def test_alt_text_unhelpful_produces_issue_with_suggested_alt():
    page = FakePage(
        alt_images=[{
            "alt": "image.jpg",
            "src": "/image.jpg", "selector": "#pic", "area": 60000,
            "html": "<img id='pic'>",
        }],
        rect_by_selector={"#pic": {
            "x": 0, "y": 0, "w": 200, "h": 200, "vw": 1024, "vh": 768,
        }},
    )

    def stub(messages, *, api_key, model):
        # Confirm the stub actually receives a vision payload — otherwise
        # we'd be silently skipping the screenshot path.
        user = messages[-1]["content"]
        assert isinstance(user, list)
        assert any(c.get("type") == "image_url" for c in user)
        return {
            "helpful": False,
            "reason": "Alt is the filename, not a description.",
            "better_alt": "Team photo at the 2025 summit.",
        }

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    issues = [i for i in result["issues"] if i["rule"] == "vlm-alt-unhelpful"]
    assert len(issues) == 1
    issue = issues[0]
    assert issue["severity"] == "moderate"
    assert issue["wcag_criteria"] == ["1.1.1"]
    assert issue["confidence"] == "medium"
    assert issue["details"]["suggested_alt"] == "Team photo at the 2025 summit."
    assert issue["module"] == "vlm"


def test_alt_text_respects_max_check_cap(monkeypatch):
    """MAX_ALT_CHECKS bounds the number of images we send."""
    monkeypatch.setattr(vlm, "MAX_ALT_CHECKS", 2)
    images = [
        {"alt": f"img {i}", "src": f"/{i}.jpg", "selector": f"#i{i}",
         "area": 60000, "html": f"<img id='i{i}'>"}
        for i in range(5)
    ]
    rects = {
        f"#i{i}": {"x": 0, "y": 0, "w": 50, "h": 50, "vw": 1024, "vh": 768}
        for i in range(5)
    }
    page = FakePage(alt_images=images, rect_by_selector=rects)

    calls = {"n": 0}

    def stub(_m, *, api_key, model):
        calls["n"] += 1
        return {"helpful": False, "reason": "x", "better_alt": "y"}

    opts = _call_with_stub(stub)
    vlm.run(page, options=opts)
    # Exactly MAX_ALT_CHECKS calls — never more.
    assert calls["n"] == 2


# ---------------------------------------------------------------------
# Heading-visual mismatch.


def test_heading_visual_missing_candidate_flagged():
    page = FakePage(
        headings={
            "headings": [
                {"level": 1, "tag": "h1", "text": "Welcome",
                 "font_size": 32, "font_weight": "bold", "selector": "h1"},
            ],
            "candidates": [
                {"tag": "div", "text": "Our Products", "font_size": 28,
                 "font_weight": "bold", "selector": "div.section-title"},
            ],
        },
    )

    def stub(_m, *, api_key, model):
        return {
            "visual_headings_missing": [{
                "selector": "div.section-title",
                "text": "Our Products",
                "suggested_level": 2,
            }],
            "hierarchy_mismatches": [],
        }

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    rules = [i["rule"] for i in result["issues"]]
    assert "vlm-visual-heading-missing" in rules
    issue = next(i for i in result["issues"]
                 if i["rule"] == "vlm-visual-heading-missing")
    assert issue["wcag_criteria"] == ["1.3.1"]
    assert issue["details"]["suggested_level"] == 2


def test_heading_hierarchy_mismatch_flagged():
    page = FakePage(
        headings={
            "headings": [
                {"level": 4, "tag": "h4", "text": "Big heading",
                 "font_size": 48, "font_weight": "bold", "selector": "h4"},
                {"level": 2, "tag": "h2", "text": "Small heading",
                 "font_size": 16, "font_weight": "normal", "selector": "h2"},
            ],
            "candidates": [],
        },
    )

    def stub(_m, *, api_key, model):
        return {
            "visual_headings_missing": [],
            "hierarchy_mismatches": [{
                "selector": "h4",
                "reason": "h4 is visually larger than h2 that follows.",
            }],
        }

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    issues = [i for i in result["issues"]
              if i["rule"] == "vlm-heading-hierarchy-mismatch"]
    assert len(issues) == 1
    assert set(issues[0]["wcag_criteria"]) >= {"1.3.1", "2.4.6"}


def test_heading_empty_result_no_issues():
    page = FakePage(
        headings={"headings": [{"level": 1, "tag": "h1", "text": "OK",
                                "font_size": 32, "font_weight": "bold",
                                "selector": "h1"}],
                  "candidates": []},
    )

    def stub(_m, *, api_key, model):
        return {"visual_headings_missing": [], "hierarchy_mismatches": []}

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    vlm_rules = [i["rule"] for i in result["issues"] if i["module"] == "vlm"]
    assert not any(r.startswith("vlm-heading") or r.startswith("vlm-visual-heading")
                   for r in vlm_rules)


# ---------------------------------------------------------------------
# Link-text meaningfulness.


def test_link_ambiguous_link_flagged_only_when_selector_matches():
    """The LLM response must reference a selector from the actual input —
    dropped otherwise, so the model can't invent issues about elements
    it didn't see."""
    page = FakePage(
        links=[{
            "text": "click here", "href": "/docs",
            "parent_text": "", "selector": "a#bad",
        }],
    )

    def stub(_m, *, api_key, model):
        return {
            "ambiguous": [
                {"selector": "a#bad", "reason": "no context",
                 "suggested_text": "Read the docs"},
                {"selector": "a#ghost", "reason": "hallucinated", "suggested_text": "X"},
            ],
        }

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    hits = [i for i in result["issues"] if i["rule"] == "vlm-link-ambiguous"]
    assert len(hits) == 1
    assert hits[0]["element"]["selector"] == "a#bad"
    assert "Read the docs" in hits[0]["details"]["suggested_text"]


def test_link_no_ambiguous_no_issues():
    page = FakePage(
        links=[{"text": "Contact support", "href": "/s",
                "parent_text": "", "selector": "a.one"}],
    )

    def stub(_m, *, api_key, model):
        return {"ambiguous": []}

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    assert not any(i["rule"] == "vlm-link-ambiguous" for i in result["issues"])


# ---------------------------------------------------------------------
# Error-message clarity.


def test_error_unclear_flagged():
    page = FakePage(
        errors=[{"text": "Invalid", "selector": ".error"}],
    )

    def stub(_m, *, api_key, model):
        return {"unclear": [{
            "selector": ".error",
            "reason": "'Invalid' doesn't explain what or how to fix",
            "suggested_text": "Enter a valid email (e.g. you@example.com).",
        }]}

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    hits = [i for i in result["issues"] if i["rule"] == "vlm-error-unclear"]
    assert len(hits) == 1
    assert hits[0]["wcag_criteria"] == ["3.3.3"]
    assert "you@example.com" in hits[0]["details"]["suggested_text"]


def test_no_error_elements_returns_empty():
    page = FakePage(errors=[])

    def stub(_m, *, api_key, model):
        raise AssertionError("should not be called when no errors present")

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    assert not any(i["rule"] == "vlm-error-unclear" for i in result["issues"])


# ---------------------------------------------------------------------
# Cross-check behaviors.


def test_alt_call_failure_does_not_sink_other_checks():
    """A VLM call crash inside the alt-text loop must not prevent the
    link check from running and producing its own issues."""
    page = FakePage(
        alt_images=[{"alt": "image.jpg", "src": "/x.jpg", "selector": "#x",
                     "area": 60000, "html": "<img id='x'>"}],
        rect_by_selector={"#x": {"x": 0, "y": 0, "w": 50, "h": 50,
                                 "vw": 1024, "vh": 768}},
        links=[{"text": "here", "href": "/x", "parent_text": "",
                "selector": "a.here"}],
    )

    def stub(messages, *, api_key, model):
        # Crash the alt-text call (identifiable by vision payload).
        if isinstance(messages[-1]["content"], list):
            raise RuntimeError("simulated alt-text failure")
        return {"ambiguous": [{"selector": "a.here", "reason": "bad",
                               "suggested_text": "Read the pricing page"}]}

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    # Link check still produced its issue.
    assert any(i["rule"] == "vlm-link-ambiguous" for i in result["issues"])
    # No alt issues (call failed silently — that's by-design resilience,
    # per-image failures skip the image rather than crashing the check).
    assert not any(i["rule"] == "vlm-alt-unhelpful" for i in result["issues"])


def test_run_returns_model_and_duration():
    page = FakePage()

    def stub(_m, *, api_key, model):
        return {}

    opts = _call_with_stub(stub)
    result = vlm.run(page, options=opts)
    assert result["ran"] is True
    assert result["model"] == "stub-model"
    assert result["duration_seconds"] >= 0


def test_env_var_enables_vlm(monkeypatch):
    """VLM_CHECKS_ENABLED env var should opt in without the option flag."""
    monkeypatch.setenv("VLM_CHECKS_ENABLED", "1")
    page = FakePage()

    def stub(_m, *, api_key, model):
        return {}

    vlm.set_vlm_caller(stub)
    result = vlm.run(page, options={})
    assert result["ran"] is True
