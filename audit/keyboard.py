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
    function cssPath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
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
        page.keyboard.press("Tab")
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

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "tab_stops": stops,
        "tab_stops_count": len(stops),
        "traps_found": sum(1 for i in issues if i["rule"] == "keyboard-trap-suspected"),
        "cycled": cycled,
    }
