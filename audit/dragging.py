"""Dragging Movements module — WCAG 2.5.7 (AA, new in 2.2).

> "All functionality that uses a dragging movement for operation can
> be achieved by a single pointer without dragging, unless dragging
> is essential or the functionality is determined by the user agent
> and not modified by the author."

Pure-static automation can't *prove* a drag-only flow exists — that
needs interaction recording. But it can detect the strong signals:

  1. Elements with `draggable="true"` plus an inline ondragstart /
     ondrop handler, **without** a sibling button/anchor that could
     stand in as a single-pointer alternative.
  2. Elements bound via well-known drag-only library handlers
     (sortable.js, react-dnd backends, dragula classes — checked at a
     class-name level).
  3. Slider widgets (`role="slider"`, `<input type="range">`) without
     keyboard arrow-key handling. Sliders are pointer-draggable by
     default; HTML's `<input type="range">` provides keyboard support
     natively, custom ARIA sliders often don't.

Rules emitted:

- `dragging-handler-on-element`  WCAG 2.5.7  moderate  draggable element
                                                       with no keyboard / button alternative
- `dragging-no-keyboard-alt`     WCAG 2.5.7  serious   slider/sortable widget that
                                                       cannot be operated without dragging

Heuristic — confidence is `medium` because we cannot inspect runtime
keyboard handlers from a static audit. Real verification requires the
dynamic DSL.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

_PROBE_JS = r"""
() => {
    function cssPath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + CSS.escape(el.id);
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
    function visible(el) {
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) return false;
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        return true;
    }
    // Drag handlers + library-class signal. We deliberately keep the
    // class regex short — a long one false-positives on Tailwind utility
    // names like "draggable" used purely visually.
    const DRAG_CLASS = /\b(sortable|drag-handle|gu-handle|dnd-item)\b/;

    const draggables = [];
    for (const el of document.querySelectorAll(
        '[draggable="true"], [ondragstart], [ondrop], [class*="sortable"], '
        + '[class*="drag-handle"], [class*="gu-handle"]'
    )) {
        if (!visible(el)) continue;
        const cls = (el.className && el.className.baseVal) || el.className || '';
        const has_class_signal = typeof cls === 'string' && DRAG_CLASS.test(cls);
        const has_attr_signal = (
            el.getAttribute('draggable') === 'true'
            || el.hasAttribute('ondragstart')
            || el.hasAttribute('ondrop')
        );
        if (!has_class_signal && !has_attr_signal) continue;

        // Look for a "single-pointer alternative" near this element:
        // a button/link sibling that could plausibly replace the drag.
        // This is the cheapest precision boost — drag-and-drop UIs
        // that ship with explicit "Move up / Move down" buttons are
        // already 2.5.7-compliant.
        const parent = el.parentElement;
        let has_alt_button = false;
        if (parent) {
            const siblings = parent.querySelectorAll('button, a[href], [role="button"]');
            for (const s of siblings) {
                const t = (s.innerText || '').toLowerCase();
                if (/move|reorder|up|down|swap|sort/.test(t)) {
                    has_alt_button = true;
                    break;
                }
            }
        }

        draggables.push({
            tag: el.tagName.toLowerCase(),
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
            class_signal: has_class_signal,
            attr_signal: has_attr_signal,
            has_sibling_alternative: has_alt_button,
        });
        if (draggables.length >= 30) break;
    }

    // Custom ARIA sliders without an HTML range fallback are a strong
    // 2.5.7 risk — most ship with mouse-only handling. We surface them
    // and let the analyzer tier severity by whether they sit alongside
    // a real <input type="range">.
    const sliders = [];
    for (const el of document.querySelectorAll('[role="slider"]')) {
        if (!visible(el)) continue;
        // Does this slider have a keydown handler binding registered
        // via an attribute? We can't see addEventListener bindings
        // here, but `onkeydown=` and `tabindex=` are visible signals.
        const has_keydown = el.hasAttribute('onkeydown');
        const tabindex = el.getAttribute('tabindex');
        const tabbable = tabindex !== null && parseInt(tabindex) >= 0;
        sliders.push({
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
            has_keydown_attr: has_keydown,
            tabbable,
        });
        if (sliders.length >= 20) break;
    }

    return {draggables, sliders};
}
"""


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for idx, item in enumerate(probe.get("draggables") or []):
        # If a "Move up / Move down" sibling button exists, the SC's
        # alternative requirement is satisfied — don't fire.
        if item.get("has_sibling_alternative"):
            continue
        issues.append(make_issue(
            issue_id=f"dragging-handler-on-element-{idx}",
            module="dragging",
            rule="dragging-handler-on-element",
            severity="moderate",
            wcag=["2.5.7"],
            confidence="medium",
            title=(
                f"Draggable <{item.get('tag', '?')}> has no obvious "
                "single-pointer alternative"
            ),
            description=(
                "WCAG 2.5.7 (AA, new in 2.2) requires every dragging "
                "movement to have a single-pointer alternative. This "
                "element is configured for drag-and-drop "
                "(`draggable=\"true\"`, an `ondragstart` handler, or a "
                "known drag library class), but no nearby button or "
                "link with reorder-style wording (\"Move up\", \"Sort\", "
                "etc.) was detected to serve as the alternative. "
                "Heuristic — review and dismiss if a non-drag flow "
                "exists out of view."
            ),
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            details={
                "class_signal": item.get("class_signal"),
                "attr_signal": item.get("attr_signal"),
            },
            fix=(
                "Add visible buttons that perform the same operation "
                "without dragging — e.g. \"Move up\" / \"Move down\" "
                "for sortable lists, or a numeric input for sliders. "
                "If the drag is essential to the task (signature "
                "panes, drawing tools), document the reasoning and "
                "this rule can be dismissed."
            ),
        ))

    for idx, item in enumerate(probe.get("sliders") or []):
        # A slider with neither tabindex nor an onkeydown is almost
        # certainly mouse-only.
        if item.get("has_keydown_attr") or item.get("tabbable"):
            continue
        issues.append(make_issue(
            issue_id=f"dragging-no-keyboard-alt-{idx}",
            module="dragging",
            rule="dragging-no-keyboard-alt",
            severity="serious",
            wcag=["2.5.7", "2.1.1"],
            confidence="medium",
            title=(
                "Custom ARIA slider has no detectable keyboard support"
            ),
            description=(
                "This element declares role=\"slider\" but is neither "
                "tabbable (no `tabindex`) nor wired to a keyboard "
                "handler attribute. Custom ARIA sliders need explicit "
                "ArrowLeft / ArrowRight handling to provide a "
                "single-pointer alternative to dragging, otherwise "
                "users without fine-motor control or a mouse cannot "
                "operate it. WCAG 2.5.7 (AA) and 2.1.1 (A) both fail."
            ),
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            fix=(
                "Either replace the custom slider with `<input "
                "type=\"range\">` (keyboard support is built in), or "
                "add `tabindex=\"0\"` and a keydown handler that "
                "responds to ArrowLeft/Right (and ideally Home/End, "
                "PageUp/Down) per the WAI-ARIA slider pattern."
            ),
        ))

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("dragging probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "draggable_candidates": len((probe or {}).get("draggables") or []),
        "slider_candidates": len((probe or {}).get("sliders") or []),
    }
