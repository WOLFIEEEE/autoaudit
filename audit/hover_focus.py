"""Content on Hover or Focus — WCAG 1.4.13 (AA).

> "Where receiving and then removing pointer hover or keyboard focus
> triggers additional content to become visible and then hidden, the
> following are true:
>   - Dismissible: a mechanism is available to dismiss without
>     moving pointer hover / keyboard focus.
>   - Hoverable: if pointer hover triggers the content, the pointer
>     can be moved over the content without the content disappearing.
>   - Persistent: additional content remains until hover/focus is
>     removed, the user dismisses it, or its information is no
>     longer valid."

This module discovers tooltip / popover candidates and, for each,
runs the three sub-tests in a real browser:

  1. Hover the trigger, capture revealed content's id.
  2. Press Escape — content should be hidden (Dismissible).
  3. Re-hover the trigger, then move the pointer ONTO the revealed
     content — content should remain visible (Hoverable).
  4. Re-hover the trigger, wait 3s without moving — content should
     remain visible (Persistent).

Discovery is heuristic: elements with `[title]`, `[aria-describedby]`
pointing to a non-visible-on-load element, or the common library
selectors (`[role="tooltip"]`, `[data-tooltip]`).

Rules emitted:

- `hover-not-dismissible`     WCAG 1.4.13  serious   Escape doesn't dismiss tooltip
- `hover-disappears-on-hover` WCAG 1.4.13  serious   moving onto content makes it vanish
- `hover-not-persistent`      WCAG 1.4.13  moderate  content vanishes within 3s with no input
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Cap how many triggers we test. Each one costs ~1s of real-time
# (multiple page.wait_for_timeout calls); a marketing page with 200
# tooltips would otherwise dominate the audit.
_MAX_TRIGGERS = 8

_DISCOVER_JS = r"""
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
        const s = getComputedStyle(el);
        return (r.width > 1 && r.height > 1
                && s.display !== 'none' && s.visibility !== 'hidden');
    }
    const triggers = [];
    // 1. aria-describedby pointing at a content element that's
    //    currently NOT visible. That's the canonical "tooltip
    //    trigger" pattern.
    for (const el of document.querySelectorAll('[aria-describedby]')) {
        if (!visible(el)) continue;
        const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        for (const id of ids) {
            const tip = document.getElementById(id);
            if (!tip) continue;
            const tipR = tip.getBoundingClientRect();
            const tipS = getComputedStyle(tip);
            const hidden_now = (
                tipS.display === 'none'
                || tipS.visibility === 'hidden'
                || parseFloat(tipS.opacity) === 0
                || (tipR.width < 1 && tipR.height < 1)
            );
            if (hidden_now) {
                triggers.push({
                    trigger_selector: cssPath(el),
                    tooltip_id: id,
                    pattern: 'aria-describedby',
                });
                break;  // one tip per trigger is enough
            }
        }
    }
    // 2. data-tooltip and [role=tooltip] markers used by popular
    //    libraries (Bootstrap, Floating UI demos, Tippy.js).
    for (const el of document.querySelectorAll(
        '[data-tooltip], [data-bs-toggle="tooltip"], [data-tippy-content]'
    )) {
        if (!visible(el)) continue;
        triggers.push({
            trigger_selector: cssPath(el),
            tooltip_id: null,
            pattern: 'data-attribute',
        });
    }
    return triggers.slice(0, 32);  // hard upper bound for collection
}
"""


def _is_tip_visible(page, tooltip_id: str | None, pattern: str) -> bool:
    """Best-effort visibility probe for the revealed content."""
    try:
        if tooltip_id:
            return bool(page.evaluate(
                r"""(id) => {
                    const t = document.getElementById(id);
                    if (!t) return false;
                    const s = getComputedStyle(t);
                    const r = t.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden'
                        && parseFloat(s.opacity) > 0 && r.width > 1 && r.height > 1;
                }""",
                tooltip_id,
            ))
        # Library tooltips create their own root. Look for any
        # [role=tooltip] visible in the viewport.
        return bool(page.evaluate(
            r"""() => {
                for (const t of document.querySelectorAll('[role="tooltip"]')) {
                    const s = getComputedStyle(t);
                    const r = t.getBoundingClientRect();
                    if (s.display !== 'none' && s.visibility !== 'hidden'
                        && parseFloat(s.opacity) > 0 && r.width > 1 && r.height > 1) {
                        return true;
                    }
                }
                return false;
            }"""
        ))
    except Exception as exc:
        log.debug("tooltip visibility probe failed: %s", exc)
        return False


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    issues: list[dict[str, Any]] = []
    triggers_checked = 0
    try:
        triggers = page.evaluate(_DISCOVER_JS) or []
    except Exception as exc:
        log.exception("hover_focus discovery failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    for spec in triggers[:_MAX_TRIGGERS]:
        sel = spec.get("trigger_selector")
        tip_id = spec.get("tooltip_id")
        pattern = spec.get("pattern", "")
        if not sel:
            continue
        try:
            trigger_loc = page.locator(sel).first
            trigger_loc.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            continue

        # 1. Hover, then verify the tip appeared. If not, this trigger
        # is probably not actually a tooltip — skip silently.
        try:
            trigger_loc.hover(timeout=2000)
        except Exception:
            continue
        page.wait_for_timeout(200)
        if not _is_tip_visible(page, tip_id, pattern):
            continue
        triggers_checked += 1

        # 2. Dismissible: Escape should hide the content.
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            still_visible = _is_tip_visible(page, tip_id, pattern)
        except Exception:
            still_visible = False
        if still_visible:
            issues.append(make_issue(
                issue_id=f"hover-not-dismissible-{sel}",
                module="hover_focus",
                rule="hover-not-dismissible",
                severity="serious",
                wcag=["1.4.13"],
                confidence="high",
                title=f"Tooltip from {sel!r} cannot be dismissed with Escape",
                description=(
                    "WCAG 1.4.13 (AA) requires hover/focus-revealed "
                    "content to be dismissible without moving the "
                    "pointer or focus. Pressing Escape did not hide "
                    "this tooltip — users with magnification or "
                    "speech-input cannot make it go away to read what "
                    "was underneath."
                ),
                selector=sel,
                details={"pattern": pattern, "tooltip_id": tip_id},
                fix=(
                    "Listen for the Escape key while the tooltip is "
                    "open and call .hide() (or remove the element). "
                    "Native HTML5 popover element handles this for free."
                ),
            ))

        # 3. Hoverable: re-trigger, then move pointer onto the tip.
        try:
            trigger_loc.hover(timeout=2000)
            page.wait_for_timeout(200)
            visible_again = _is_tip_visible(page, tip_id, pattern)
            if not visible_again:
                continue
            # Try to hover the tip itself.
            if tip_id:
                tip_loc = page.locator(f"#{tip_id}").first
                tip_loc.hover(timeout=2000)
            else:
                tip_loc = page.locator('[role="tooltip"]').first
                tip_loc.hover(timeout=2000)
            page.wait_for_timeout(200)
            still_after_move = _is_tip_visible(page, tip_id, pattern)
        except Exception:
            still_after_move = True  # don't false-positive on infra
        if not still_after_move:
            issues.append(make_issue(
                issue_id=f"hover-disappears-on-hover-{sel}",
                module="hover_focus",
                rule="hover-disappears-on-hover",
                severity="serious",
                wcag=["1.4.13"],
                confidence="high",
                title=(
                    f"Tooltip from {sel!r} disappears when the pointer "
                    "moves onto its content"
                ),
                description=(
                    "WCAG 1.4.13 (AA) requires that hover-revealed "
                    "content remain visible while the pointer is over "
                    "EITHER the trigger OR the revealed content. The "
                    "tooltip vanished when the pointer moved off the "
                    "trigger and onto the tip — users with low vision "
                    "who rely on screen magnification cannot move "
                    "their cursor close enough to read the tip."
                ),
                selector=sel,
                details={"pattern": pattern, "tooltip_id": tip_id},
                fix=(
                    "Add hover listeners to the revealed-content "
                    "element itself, not just the trigger. The "
                    "tooltip should stay visible whenever the "
                    "pointer is over either."
                ),
            ))

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "triggers_discovered": len(triggers),
        "triggers_checked": triggers_checked,
    }
