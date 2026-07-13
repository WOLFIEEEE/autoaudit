"""VLM-judged semantic accessibility checks.

Four checks that rules can't do reliably — they need a vision-capable
LLM's judgement:

  - alt-text usefulness (WCAG 1.1.1)
      Every image with non-empty alt: does the alt text actually
      convey what a sighted user sees?
  - heading-visual hierarchy mismatch (WCAG 1.3.1 / 2.4.6)
      Text that *looks* like a heading but isn't marked up as one, and
      DOM heading levels that don't match visual prominence.
  - link-text meaningfulness (WCAG 2.4.4)
      Beyond the static "click here" phrase list — links whose purpose
      isn't clear from text or immediate context.
  - error-message clarity (WCAG 3.3.3)
      Form errors that don't tell the user HOW to fix the problem.

Fails CLOSED. If `OPENROUTER_API_KEY` is unset, or `options["vlm_checks"]`
is not truthy, this module returns `ran=False, skipped=True` with no
issues. Each check is individually budget-capped and any failure in one
check does NOT sink the others — a flaky VLM response for alt-text
won't lose link-text results.

Reading level (3.1.5) sits in `audit.cognitive` — Flesch-Kincaid is a
settled WCAG proxy and doesn't need an LLM.

Test seam: `set_vlm_caller(fn)` replaces the network call with a stub.
Tests pass a function that returns canned JSON dicts.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Callable

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Vision-capable default. Overridable via env so deployments can pin a
# specific snapshot or switch providers without code changes.
DEFAULT_MODEL = os.environ.get("OPENROUTER_VLM_MODEL", "anthropic/claude-sonnet-4-6")


def _env_number(name: str, default: float, *, cast: type) -> float:
    """Parse a numeric env var defensively. Misconfigured values fall
    back to the documented default and surface a warning, rather than
    crashing the audit at import time."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return cast(default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        log.warning("env %s=%r is not a valid %s; using default %s",
                    name, raw, cast.__name__, default)
        return cast(default)


DEFAULT_TIMEOUT = _env_number("OPENROUTER_VLM_TIMEOUT", 60.0, cast=float)

# Per-check caps. Each alt-text call is one network round-trip so this
# cap dominates cost. Link/error checks batch in a single call so their
# caps are looser.
MAX_ALT_CHECKS = int(_env_number("VLM_MAX_ALT_CHECKS", 15, cast=int))
MAX_LINK_CHECKS = int(_env_number("VLM_MAX_LINK_CHECKS", 30, cast=int))
MAX_ERROR_CHECKS = int(_env_number("VLM_MAX_ERROR_CHECKS", 10, cast=int))


# Dependency-injection hook. Tests replace this with a stub that returns
# canned JSON — no network, no API key required.
_call_vlm_fn: Callable[..., Any] | None = None


def set_vlm_caller(fn: Callable[..., Any] | None) -> None:
    """Test hook: replace the network VLM caller with a stub.

    The stub signature is `fn(messages, *, api_key, model) -> dict`.
    Pass None to restore the real caller.
    """
    global _call_vlm_fn
    _call_vlm_fn = fn


# --------------------------------------------------------------------------
# Main entry point — registered in orchestrator alongside keyboard/forms/etc.


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:
    start = time.time()
    options = options or {}

    # Opt-in. We don't want an accidental 50-call VLM bill the first time
    # someone flips on the audit. Explicit opt-in via option OR env var.
    enabled = options.get("vlm_checks") or os.environ.get("VLM_CHECKS_ENABLED")
    if not enabled:
        return _skipped(start, "vlm_checks option not enabled")

    api_key = options.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY")
    if not api_key and _call_vlm_fn is None:
        # Real-network path needs a key; stub path does not.
        return _skipped(start, "OPENROUTER_API_KEY not set")

    model = options.get("vlm_model") or DEFAULT_MODEL

    issues: list[dict[str, Any]] = []
    checks_ran: dict[str, dict[str, Any]] = {}

    for name, fn in [
        ("alt_text",       lambda: _check_alt_text(page, api_key=api_key, model=model)),
        ("heading_visual", lambda: _check_heading_visual(page, api_key=api_key, model=model)),
        ("link_text",      lambda: _check_link_text(page, api_key=api_key, model=model)),
        ("error_messages", lambda: _check_error_messages(page, api_key=api_key, model=model)),
    ]:
        try:
            check_issues, elements_checked = fn()
            issues.extend(check_issues)
            checks_ran[name] = {
                "ok": True,
                "elements_checked": elements_checked,
                "issues_found": len(check_issues),
            }
        except Exception as exc:
            log.warning("vlm.%s failed: %s", name, exc)
            checks_ran[name] = {"ok": False, "error": str(exc)}

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 2),
        "checks": checks_ran,
        "model": model,
    }


def _skipped(start: float, reason: str) -> dict[str, Any]:
    return {
        "ran": False,
        "skipped": True,
        "reason": reason,
        "issues": [],
        "duration_seconds": round(time.time() - start, 2),
    }


# --------------------------------------------------------------------------
# VLM call plumbing.


def _default_call_vlm(
    messages: list[dict[str, Any]],
    *,
    api_key: str,
    model: str,
) -> Any:
    import httpx

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://github.com/autoaudit"),
        "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Autoaudit"),
    }
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        resp = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"unexpected OpenRouter response: {exc}") from exc
    return json.loads(content)


def _call(messages: list[dict[str, Any]], *, api_key: str, model: str) -> Any:
    fn = _call_vlm_fn or _default_call_vlm
    return fn(messages, api_key=api_key, model=model)


def _capture_element_png(page, selector: str) -> bytes | None:
    """Return un-annotated PNG bytes of the element, or None if unavailable."""
    rect = page.evaluate(
        r"""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            el.scrollIntoView({block: 'center', inline: 'center'});
            const r = el.getBoundingClientRect();
            return {
                x: r.left, y: r.top, w: r.width, h: r.height,
                vw: window.innerWidth, vh: window.innerHeight,
            };
        }""",
        selector,
    )
    if not rect or rect["w"] < 1 or rect["h"] < 1:
        return None
    x0 = max(0, int(rect["x"]))
    y0 = max(0, int(rect["y"]))
    x1 = min(int(rect["vw"]), int(rect["x"] + rect["w"]))
    y1 = min(int(rect["vh"]), int(rect["y"] + rect["h"]))
    if x1 <= x0 or y1 <= y0:
        return None
    return page.screenshot(
        clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
        type="png",
    )


# --------------------------------------------------------------------------
# Check 1: alt-text usefulness (WCAG 1.1.1).


_ALT_EXTRACT_JS = r"""() => {
    function path(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sib = [...parent.children].filter(c => c.tagName === cur.tagName);
                if (sib.length > 1) part += ':nth-of-type(' + (sib.indexOf(cur) + 1) + ')';
            }
            parts.unshift(part);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    const imgs = [...document.querySelectorAll('img[alt]')].filter(img => {
        const alt = img.getAttribute('alt');
        if (!alt || alt.trim().length === 0) return false;
        if (img.getAttribute('role') === 'presentation') return false;
        if (img.getAttribute('aria-hidden') === 'true') return false;
        const r = img.getBoundingClientRect();
        return r.width >= 40 && r.height >= 40;
    });
    return imgs.map(img => {
        const r = img.getBoundingClientRect();
        return {
            alt: img.getAttribute('alt'),
            src: (img.getAttribute('src') || '').slice(0, 200),
            selector: path(img),
            area: (r.width * r.height) | 0,
            html: img.outerHTML.slice(0, 200),
        };
    }).sort((a, b) => b.area - a.area).slice(0, 40);
}"""


def _check_alt_text(page, *, api_key: str, model: str) -> tuple[list[dict[str, Any]], int]:
    imgs = page.evaluate(_ALT_EXTRACT_JS)
    imgs = imgs[:MAX_ALT_CHECKS]
    issues: list[dict[str, Any]] = []
    for idx, img in enumerate(imgs):
        png = _capture_element_png(page, img["selector"])
        if not png:
            continue
        b64 = base64.b64encode(png).decode("ascii")
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an accessibility expert evaluating image alt text. "
                    "For a SIGHTED user viewing the image, does the given alt "
                    "text convey the same essential information? Be conservative: "
                    "only flag alt text that is clearly unhelpful (filename, "
                    "generic word like 'image', or entirely misrepresents the "
                    "image). Short-but-accurate alt text is fine.\n\n"
                    "Return JSON: {\"helpful\": true|false, \"reason\": \"...\", "
                    "\"better_alt\": \"suggested replacement if helpful is false\"}."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Alt text: {img['alt']!r}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            },
        ]
        try:
            result = _call(messages, api_key=api_key, model=model)
        except Exception as exc:
            log.debug("vlm alt-text failed for %s: %s", img["selector"], exc)
            continue
        if not isinstance(result, dict) or result.get("helpful"):
            continue
        issues.append(make_issue(
            issue_id=f"vlm-alt-unhelpful-{idx}",
            module="vlm",
            rule="vlm-alt-unhelpful",
            severity="moderate",
            wcag=["1.1.1"],
            title=f"Alt text may not describe the image: {img['alt']!r}",
            description=(
                "A vision model compared the image to its alt text and judged "
                "that the alt does not accurately convey what a sighted user sees."
            ),
            selector=img["selector"],
            html_snippet=img["html"],
            confidence="medium",
            details={
                "current_alt": img["alt"],
                "reason": str(result.get("reason") or "")[:500],
                "suggested_alt": str(result.get("better_alt") or "")[:300],
            },
            fix="Rewrite the alt text to convey the image's essential meaning for a non-sighted user.",
        ))
    return issues, len(imgs)


# --------------------------------------------------------------------------
# Check 2: heading-visual hierarchy mismatch (WCAG 1.3.1 / 2.4.6).


_HEADING_EXTRACT_JS = r"""() => {
    function path(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sib = [...parent.children].filter(c => c.tagName === cur.tagName);
                if (sib.length > 1) part += ':nth-of-type(' + (sib.indexOf(cur) + 1) + ')';
            }
            parts.unshift(part);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')]
        .map(h => {
            const s = getComputedStyle(h);
            return {
                level: parseInt(h.tagName[1]),
                tag: h.tagName.toLowerCase(),
                text: (h.textContent || '').trim().slice(0, 140),
                font_size: parseFloat(s.fontSize),
                font_weight: s.fontWeight,
                selector: path(h),
            };
        }).filter(h => h.text);
    const candidates = [];
    for (const el of document.querySelectorAll('div, span, p, strong, b')) {
        if (candidates.length >= 30) break;
        if (el.closest('h1, h2, h3, h4, h5, h6')) continue;
        if (el.closest('nav, footer, header[role="banner"]')) continue;
        const txt = (el.textContent || '').trim();
        if (!txt || txt.length > 120) continue;
        const s = getComputedStyle(el);
        const fs = parseFloat(s.fontSize);
        const fw = s.fontWeight;
        const isBold = fw === 'bold' || parseInt(fw) >= 600;
        if (fs >= 20 && (isBold || fs >= 24)) {
            const r = el.getBoundingClientRect();
            if (r.width < 50 || r.height < 20) continue;
            candidates.push({
                tag: el.tagName.toLowerCase(),
                text: txt.slice(0, 140),
                font_size: fs,
                font_weight: fw,
                selector: path(el),
            });
        }
    }
    return {headings, candidates};
}"""


def _check_heading_visual(page, *, api_key: str, model: str) -> tuple[list[dict[str, Any]], int]:
    data = page.evaluate(_HEADING_EXTRACT_JS)
    headings = data.get("headings") or []
    candidates = data.get("candidates") or []
    if not headings and not candidates:
        return [], 0

    try:
        png = page.screenshot(full_page=False, type="png")
    except Exception:
        png = None

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "DOM headings (document order):\n"
                + json.dumps(headings, indent=2)
                + "\n\nVisual candidates (text blocks that LOOK like headings but are not h1-h6):\n"
                + json.dumps(candidates, indent=2)
            ),
        },
    ]
    if png:
        b64 = base64.b64encode(png).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an accessibility expert auditing page heading structure. "
                "Given the DOM heading list, a list of visual candidates, and a "
                "screenshot, answer two questions:\n"
                "1. Which visual candidates are so prominent that screen-reader "
                "users would expect them to be real h1-h6 elements?\n"
                "2. Does the DOM heading sequence match visual hierarchy? (A "
                "visually-small h2 below a visually-large h4 is a mismatch.)\n\n"
                "Be conservative. Only flag clear-cut cases. Return JSON: "
                "{\"visual_headings_missing\": [{\"selector\": \"...\", \"text\": "
                "\"...\", \"suggested_level\": 2}], \"hierarchy_mismatches\": "
                "[{\"selector\": \"...\", \"reason\": \"...\"}]}. Return empty "
                "arrays if the page's heading structure is fine."
            ),
        },
        {"role": "user", "content": content},
    ]

    try:
        result = _call(messages, api_key=api_key, model=model)
    except Exception as exc:
        log.debug("vlm heading-visual failed: %s", exc)
        return [], 0
    if not isinstance(result, dict):
        return [], 0

    issues: list[dict[str, Any]] = []
    for i, m in enumerate(result.get("visual_headings_missing") or []):
        if not isinstance(m, dict):
            continue
        sel = str(m.get("selector") or "")[:200]
        txt = str(m.get("text") or "")[:140]
        level = m.get("suggested_level")
        issues.append(make_issue(
            issue_id=f"vlm-visual-heading-missing-{i}",
            module="vlm",
            rule="vlm-visual-heading-missing",
            severity="moderate",
            wcag=["1.3.1"],
            title=f"Text appears to be a heading but is not marked up: {txt!r}",
            description=(
                "Screen readers navigate by heading hierarchy (H key). Text that "
                "looks like a heading in the visual design must also be a real "
                "heading element, or users miss it in the page outline."
            ),
            selector=sel,
            confidence="medium",
            details={"visual_text": txt, "suggested_level": level},
            fix=(
                f"Wrap this text in an <h{level}> element "
                f"(or use role=\"heading\" aria-level=\"{level}\")."
                if isinstance(level, int) else
                "Wrap this text in an appropriate heading element."
            ),
        ))
    for i, m in enumerate(result.get("hierarchy_mismatches") or []):
        if not isinstance(m, dict):
            continue
        sel = str(m.get("selector") or "")[:200]
        reason = str(m.get("reason") or "")[:300]
        issues.append(make_issue(
            issue_id=f"vlm-heading-hierarchy-mismatch-{i}",
            module="vlm",
            rule="vlm-heading-hierarchy-mismatch",
            severity="moderate",
            wcag=["1.3.1", "2.4.6"],
            title="Heading level doesn't match visual prominence",
            description=(
                "DOM heading levels don't reflect visual hierarchy. Screen-reader "
                "users rely on level order to understand outline depth."
            ),
            selector=sel,
            confidence="medium",
            details={"reason": reason},
            fix="Reorder heading levels so DOM depth matches visual/informational prominence.",
        ))
    return issues, len(headings) + len(candidates)


# --------------------------------------------------------------------------
# Check 3: link-text meaningfulness (WCAG 2.4.4).


_LINK_EXTRACT_JS = r"""() => {
    function path(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sib = [...parent.children].filter(c => c.tagName === cur.tagName);
                if (sib.length > 1) part += ':nth-of-type(' + (sib.indexOf(cur) + 1) + ')';
            }
            parts.unshift(part);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    const out = [];
    for (const a of document.querySelectorAll('a[href]')) {
        const aria = (a.getAttribute('aria-label') || '').trim();
        const txt = ((a.textContent || '').trim()).slice(0, 120);
        const name = aria || txt;
        if (!name) continue;
        if (name.length > 80) continue;
        const href = (a.getAttribute('href') || '').slice(0, 200);
        const parent = a.parentElement;
        const parent_text = parent ? (parent.textContent || '').trim().slice(0, 200) : '';
        out.push({text: name, href: href, parent_text: parent_text, selector: path(a)});
        if (out.length >= 60) break;
    }
    return out;
}"""


def _check_link_text(page, *, api_key: str, model: str) -> tuple[list[dict[str, Any]], int]:
    links = page.evaluate(_LINK_EXTRACT_JS)
    links = links[:MAX_LINK_CHECKS]
    if not links:
        return [], 0

    messages = [
        {
            "role": "system",
            "content": (
                "You are an accessibility expert evaluating link text for WCAG 2.4.4 "
                "(Link Purpose in Context). A link's text — or its text plus its "
                "immediate surrounding context — must make the destination clear. "
                "Generic phrases ('click here', 'read more', 'this page') fail. "
                "Short but specific phrases ('Contact us') are fine. Links whose "
                "purpose is clear from the surrounding paragraph are fine.\n\n"
                "Be conservative — only flag clear failures. Return JSON: "
                "{\"ambiguous\": [{\"selector\": \"...\", \"reason\": \"...\", "
                "\"suggested_text\": \"...\"}]}. Return an empty array if every "
                "link is fine."
            ),
        },
        {
            "role": "user",
            "content": "Links to evaluate:\n" + json.dumps(links, indent=2),
        },
    ]
    try:
        result = _call(messages, api_key=api_key, model=model)
    except Exception as exc:
        log.debug("vlm link-text failed: %s", exc)
        return [], 0
    if not isinstance(result, dict):
        return [], 0

    by_selector = {link["selector"]: link for link in links}
    issues: list[dict[str, Any]] = []
    for i, m in enumerate(result.get("ambiguous") or []):
        if not isinstance(m, dict):
            continue
        sel = str(m.get("selector") or "")[:200]
        matched = by_selector.get(sel)
        if matched is None:
            continue
        reason = str(m.get("reason") or "")[:300]
        suggested = str(m.get("suggested_text") or "")[:200]
        issues.append(make_issue(
            issue_id=f"vlm-link-ambiguous-{i}",
            module="vlm",
            rule="vlm-link-ambiguous",
            severity="moderate",
            wcag=["2.4.4"],
            title=f"Link text not meaningful in context: {matched['text']!r}",
            description=(
                "Screen-reader users often scan links out of context. The link's "
                "purpose must be clear from its text alone or from its immediate "
                "surrounding context."
            ),
            selector=sel,
            confidence="medium",
            details={
                "current_text": matched["text"],
                "href": matched["href"],
                "reason": reason,
                "suggested_text": suggested,
            },
            fix=(
                f"Rewrite along the lines of: {suggested!r}"
                if suggested
                else "Rewrite the link text to describe the destination or action."
            ),
        ))
    return issues, len(links)


# --------------------------------------------------------------------------
# Check 4: error-message clarity (WCAG 3.3.3).


_ERROR_EXTRACT_JS = r"""() => {
    function path(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sib = [...parent.children].filter(c => c.tagName === cur.tagName);
                if (sib.length > 1) part += ':nth-of-type(' + (sib.indexOf(cur) + 1) + ')';
            }
            parts.unshift(part);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    const sel = (
        '[role="alert"], [aria-live="assertive"], [aria-live="polite"], ' +
        '.error, .errors, .validation-error, .form-error, .field-error, ' +
        '.invalid-feedback, [data-error], [data-validation-message]'
    );
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
        const txt = (el.textContent || '').trim();
        if (!txt || txt.length < 3 || txt.length > 500) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        out.push({text: txt.slice(0, 300), selector: path(el)});
        if (out.length >= 20) break;
    }
    return out;
}"""


def _check_error_messages(page, *, api_key: str, model: str) -> tuple[list[dict[str, Any]], int]:
    errs = page.evaluate(_ERROR_EXTRACT_JS)
    errs = errs[:MAX_ERROR_CHECKS]
    if not errs:
        return [], 0

    messages = [
        {
            "role": "system",
            "content": (
                "You are an accessibility expert evaluating form error messages "
                "for WCAG 3.3.3 (Error Suggestion). A clear error tells the user "
                "WHAT went wrong AND HOW to fix it. Messages like 'Invalid', "
                "'Error', 'Required', or single-word states fail. 'Enter a valid "
                "email address (e.g. name@example.com)' passes.\n\n"
                "Return JSON: {\"unclear\": [{\"selector\": \"...\", \"reason\": "
                "\"...\", \"suggested_text\": \"...\"}]}. Return an empty array "
                "if all messages are sufficient."
            ),
        },
        {
            "role": "user",
            "content": "Error messages on the page:\n" + json.dumps(errs, indent=2),
        },
    ]
    try:
        result = _call(messages, api_key=api_key, model=model)
    except Exception as exc:
        log.debug("vlm error-msg failed: %s", exc)
        return [], 0
    if not isinstance(result, dict):
        return [], 0

    by_selector = {e["selector"]: e for e in errs}
    issues: list[dict[str, Any]] = []
    for i, m in enumerate(result.get("unclear") or []):
        if not isinstance(m, dict):
            continue
        sel = str(m.get("selector") or "")[:200]
        matched = by_selector.get(sel)
        if matched is None:
            continue
        reason = str(m.get("reason") or "")[:300]
        suggested = str(m.get("suggested_text") or "")[:300]
        issues.append(make_issue(
            issue_id=f"vlm-error-unclear-{i}",
            module="vlm",
            rule="vlm-error-unclear",
            severity="moderate",
            wcag=["3.3.3"],
            title=f"Error message unclear: {matched['text'][:60]!r}",
            description=(
                "WCAG 3.3.3 requires errors to explain what went wrong and how "
                "to correct it. A language model judged this message insufficient."
            ),
            selector=sel,
            confidence="medium",
            details={
                "current_text": matched["text"],
                "reason": reason,
                "suggested_text": suggested,
            },
            fix=(
                f"Rewrite along the lines of: {suggested!r}"
                if suggested
                else "Rewrite to explain what's wrong and how to fix it."
            ),
        ))
    return issues, len(errs)
