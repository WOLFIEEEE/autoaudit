"""Keyboard module: tab walking, focus visibility, keyboard traps.

Runs a real browser driven tab-walk: it presses Tab up to `max_tabs` times,
records what gained focus at each step, and then analyzes the resulting
tab-stop sequence for accessibility problems.

Rules implemented:
- keyboard-trap-suspected     WCAG 2.1.2  critical   focus never leaves the page within max_tabs
- keyboard-no-focus-indicator WCAG 2.4.7  serious    focused element has no visible focus style
- keyboard-no-accessible-name WCAG 4.1.2  critical   focusable element has no accessible name
- keyboard-positive-tabindex  WCAG 2.4.3  moderate   tabindex > 0 disrupts natural focus order
- keyboard-generic-focusable  WCAG 4.1.2  serious    focusable element has no semantic tag and no role attribute (<div tabindex=0>)

The walk and the analyzer are split: `_walk(page, options)` performs the
browser interaction, `analyze(stops, cycled, max_tabs)` is pure Python and
testable with fixture data.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)


_FOCUS_PROBE_JS = r"""
() => {
    // Selector builder preferring stable hooks over :nth-of-type chains.
    // Priority order (highest first):
    //   1. #id (when id is stable — not a CSS-in-JS hash)
    //   2. [data-testid=...] or [data-test=...] or [data-cy=...]
    //   3. [name=...] (for form controls)
    //   4. tag + :nth-of-type as a last resort
    // nth-of-type chains break on every DOM shuffle; test-ids don't.
    function isHashyId(id) {
        // Styled-components / emotion / CSS-modules IDs look like
        // "css-1q0lrm2" or "abc_123_def". Heuristic: rule out any id
        // that's mostly lowercase hex + dashes/underscores, or starts
        // with a prefix known to be auto-generated.
        if (!id) return true;
        if (/^(?:css|mui|chakra|sc|emotion|styled)-/i.test(id)) return true;
        if (/^[0-9a-f_-]{10,}$/i.test(id)) return true;
        return false;
    }
    function stableSelector(el) {
        if (!el || el.nodeType !== 1) return '';
        // Prefer test-id attributes — teams add these exactly so
        // automation can find elements reliably.
        for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
            const v = el.getAttribute(attr);
            if (v) return `[${attr}="${CSS.escape(v)}"]`;
        }
        if (el.id && !isHashyId(el.id)) return '#' + CSS.escape(el.id);
        // For form controls, name= is usually stable (server contract).
        if (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) {
            const n = el.getAttribute('name');
            if (n) return el.tagName.toLowerCase() + `[name="${CSS.escape(n)}"]`;
        }
        return null;
    }
    function cssPath(el) {
        if (!el || el.nodeType !== 1) return '';
        const stable = stableSelector(el);
        if (stable) return stable;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            // If we encounter a stable ancestor, anchor the selector
            // there and stop — shortens selectors dramatically on
            // pages with a few well-IDed container elements.
            const s = stableSelector(cur);
            if (s && cur !== el) {
                parts.unshift(s);
                return parts.join(' > ');
            }
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sameTag = [...parent.children].filter(c => c.tagName === cur.tagName);
                if (sameTag.length > 1) {
                    part += ':nth-of-type(' + (sameTag.indexOf(cur) + 1) + ')';
                }
            }
            parts.unshift(part);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    function accessibleName(el) {
        const aria = el.getAttribute('aria-label');
        if (aria && aria.trim()) return aria.trim();
        const labelledby = el.getAttribute('aria-labelledby');
        if (labelledby) {
            const parts = labelledby.split(/\s+/).map(id => {
                const ref = document.getElementById(id);
                return ref ? (ref.textContent || '').trim() : '';
            });
            const joined = parts.filter(Boolean).join(' ');
            if (joined) return joined;
        }
        if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
            if (el.id) {
                const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (lab) return (lab.textContent || '').trim();
            }
            const wrappingLabel = el.closest('label');
            if (wrappingLabel) return (wrappingLabel.textContent || '').trim();
            const placeholder = el.getAttribute('placeholder');
            if (placeholder) return placeholder.trim();
        }
        const text = (el.textContent || '').trim();
        if (text) return text;
        const img = el.querySelector('img[alt]');
        if (img) return (img.getAttribute('alt') || '').trim();
        const title = el.getAttribute('title');
        if (title) return title.trim();
        return '';
    }
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) {
        return { left_page: true };
    }
    const style = getComputedStyle(el);
    const outlineVisible = style.outlineStyle !== 'none'
        && parseFloat(style.outlineWidth) > 0;
    const boxShadowVisible = style.boxShadow && style.boxShadow !== 'none';
    const borderChange = parseFloat(style.borderTopWidth) > 0
        || parseFloat(style.borderBottomWidth) > 0
        || parseFloat(style.borderLeftWidth) > 0
        || parseFloat(style.borderRightWidth) > 0;
    const tag = el.tagName.toLowerCase();
    const semantic = new Set(['a','button','input','select','textarea','summary','details','label']);
    // Visible text = what a sighted user reads off the control itself.
    // innerText respects visibility (display:none, visibility:hidden),
    // so `aria-hidden` children are still counted (they're visible to
    // sighted users even though SR ignores them — that's the whole
    // point of WCAG 2.5.3 Label in Name).
    let visibleText = (el.innerText || '').trim();
    if (!visibleText) {
        // For inputs, the control itself has no text; the associated
        // <label> is the visible label.
        if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
            if (el.id) {
                const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                if (lab) visibleText = (lab.innerText || '').trim();
            }
            if (!visibleText) {
                const wrap = el.closest('label');
                if (wrap) visibleText = (wrap.innerText || '').trim();
            }
        }
    }

    return {
        left_page: false,
        tag,
        id: el.id || '',
        selector: cssPath(el),
        role: el.getAttribute('role') || '',
        has_role_attr: el.hasAttribute('role'),
        is_semantic_tag: semantic.has(tag),
        tabindex: el.tabIndex,
        accessible_name: accessibleName(el),
        visible_text: visibleText,
        outline_style: style.outlineStyle,
        outline_width: style.outlineWidth,
        box_shadow: style.boxShadow,
        has_focus_indicator: outlineVisible || boxShadowVisible || borderChange,
        html: el.outerHTML.slice(0, 200)
    };
}
"""


def _walk(page, options: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Press Tab repeatedly and record focus state.

    Returns (tab_stops, cycled_or_left_page). Duplicates a cycle-detector
    that stops when the same selector appears twice — many SPAs wrap focus
    back to the first element instead of exiting the page.
    """
    max_tabs = int(options.get("max_tabs", 100))
    wait_ms = int(options.get("wait_ms", 50))

    # Start by focusing the body so Tab proceeds from the top.
    page.evaluate("() => { document.body.focus({ preventScroll: true }); }")

    stops: list[dict[str, Any]] = []
    seen_selectors: set[str] = set()
    left_page = False

    for _ in range(max_tabs):
        # Record press_time as the wall-clock moment Tab actually
        # left Playwright's input pipeline. Path B alignment uses
        # it to match each stop to the NVDA utterances spoken in
        # the window [press_time, next_press_time) — capturing it
        # before .press() returns would shift the window earlier
        # by however long the press call blocked, drifting the
        # speech<->stop alignment whenever the page is jittery.
        page.keyboard.press("Tab")
        press_time = time.time()
        if wait_ms:
            page.wait_for_timeout(wait_ms)
        info = page.evaluate(_FOCUS_PROBE_JS)
        if info.get("left_page"):
            left_page = True
            break
        selector = info.get("selector", "")
        if selector in seen_selectors:
            # Focus wrapped. Treat as "finished cleanly" — not a trap.
            left_page = True
            break
        seen_selectors.add(selector)
        info["press_time"] = press_time
        stops.append(info)

    return stops, left_page


def analyze(
    stops: list[dict[str, Any]],
    cycled: bool,
    max_tabs: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    if not cycled and len(stops) >= max_tabs:
        issues.append(
            make_issue(
                issue_id="keyboard-trap-suspected",
                module="keyboard",
                rule="keyboard-trap-suspected",
                severity="critical",
                wcag=["2.1.2"],
                title="Possible keyboard trap",
                description=(
                    f"Tab was pressed {max_tabs} times and focus never wrapped or left "
                    "the page. Users relying on keyboard navigation may not be able "
                    "to move past this region."
                ),
                details={"tab_stops_observed": len(stops), "max_tabs": max_tabs},
                fix=(
                    "Verify that Tab can move focus out of every component. Modal "
                    "dialogs should trap focus only while open and release on close."
                ),
            )
        )

    for idx, stop in enumerate(stops):
        selector = stop.get("selector", "")
        html_snippet = stop.get("html", "")
        tag = stop.get("tag", "")
        tabindex = stop.get("tabindex", 0)

        if not stop.get("accessible_name"):
            issues.append(
                make_issue(
                    issue_id=f"keyboard-no-accessible-name-{idx}",
                    module="keyboard",
                    rule="keyboard-no-accessible-name",
                    severity="critical",
                    wcag=["4.1.2"],
                    title="Focusable element has no accessible name",
                    description=(
                        "When this element receives keyboard focus, screen readers "
                        "have nothing to announce. Users cannot identify what the "
                        "control does."
                    ),
                    selector=selector,
                    html_snippet=html_snippet,
                    details={"tag": tag, "role": stop.get("role", ""), "tab_index": idx + 1},
                    fix=(
                        "Add visible text, aria-label, or a <label for> associating "
                        "the control with a descriptive label."
                    ),
                )
            )

        if not stop.get("has_focus_indicator", True):
            issues.append(
                make_issue(
                    issue_id=f"keyboard-no-focus-indicator-{idx}",
                    module="keyboard",
                    rule="keyboard-no-focus-indicator",
                    severity="serious",
                    wcag=["2.4.7"],
                    title="Focused element has no visible focus indicator",
                    description=(
                        "Sighted keyboard users rely on a visible outline, border, "
                        "or box-shadow to know which element has focus. outline:none "
                        "with no replacement style leaves them lost."
                    ),
                    selector=selector,
                    html_snippet=html_snippet,
                    details={
                        "outline_style": stop.get("outline_style"),
                        "box_shadow": stop.get("box_shadow"),
                    },
                    fix=(
                        "Add a :focus or :focus-visible style with a clearly visible "
                        "outline, box-shadow, or border change."
                    ),
                )
            )

        if (
            not stop.get("is_semantic_tag", False)
            and not stop.get("has_role_attr", False)
        ):
            issues.append(
                make_issue(
                    issue_id=f"keyboard-generic-focusable-{idx}",
                    module="keyboard",
                    rule="keyboard-generic-focusable",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="Focusable element has no semantic tag and no role attribute",
                    description=(
                        "Screen readers can't tell users what kind of control this is. "
                        "This is the classic <div onclick> / <span tabindex> anti-pattern."
                    ),
                    selector=selector,
                    html_snippet=html_snippet,
                    details={"tag": tag, "tab_index": idx + 1},
                    fix=(
                        "Use a semantic element (<button>, <a>), or add an appropriate "
                        'role (role="button") plus keyboard handlers.'
                    ),
                )
            )

        if isinstance(tabindex, int) and tabindex > 0:
            issues.append(
                make_issue(
                    issue_id=f"keyboard-positive-tabindex-{idx}",
                    module="keyboard",
                    rule="keyboard-positive-tabindex",
                    severity="moderate",
                    wcag=["2.4.3"],
                    title=f"Element has tabindex={tabindex} (positive)",
                    description=(
                        "Positive tabindex values override the natural DOM order and "
                        "almost always create a tab sequence that surprises users."
                    ),
                    selector=selector,
                    html_snippet=html_snippet,
                    details={"tabindex": tabindex},
                    fix="Use tabindex=0 (or no tabindex) and let DOM order determine focus order.",
                )
            )

    return issues


def run(page, nvda=None, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    options = options or {}
    start = time.time()
    max_tabs = int(options.get("max_tabs", 100))

    try:
        stops, cycled = _walk(page, options)
    except Exception as exc:
        log.exception("keyboard walk failed")
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "tab_stops": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    issues = analyze(stops, cycled, max_tabs)

    # Coverage truncation: if we hit `max_tabs` without the focus cycle
    # wrapping or leaving the page, we STOPPED walking — we didn't
    # finish. Downstream consumers (report, VPAT) must not read the
    # absence of findings on un-walked elements as "pass." `truncated`
    # makes this explicit; the HTML report surfaces it as a banner.
    truncated = (not cycled) and len(stops) >= max_tabs

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "tab_stops": stops,
        "tab_stops_count": len(stops),
        "traps_found": sum(1 for i in issues if i["rule"] == "keyboard-trap-suspected"),
        "cycled": cycled,
        "coverage": {
            "max_tabs": max_tabs,
            "stops_walked": len(stops),
            "truncated": truncated,
            # When truncated, every tab-stop beyond the cap is a blind
            # spot. Flag it so report consumers don't overstate coverage.
            "note": (
                f"Walked {len(stops)}/{max_tabs} focusable elements; the "
                "tab walk hit its cap before cycling. Elements beyond "
                "this point were not evaluated."
                if truncated
                else None
            ),
        },
    }
