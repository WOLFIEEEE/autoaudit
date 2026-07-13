"""Focus Not Obscured (Minimum) — WCAG 2.4.11 (AA, new in 2.2).

> "When a user interface component receives keyboard focus, the
> component is not entirely hidden due to author-created content."

Real coverage of this needs the keyboard walk's stop list (every
focusable element + its bounding rect at the moment focus arrives)
and the position of every fixed/sticky overlay on the page. We
already collect tab stops in `audit/keyboard.py`. This module
consumes them.

Algorithm:

  For each tab stop, compute whether any sticky-or-fixed-positioned
  element (modal underlay, sticky header, cookie banner, chat widget)
  fully covers the focused element's bounding rect. "Fully covers" is
  the SC's wording — partial occlusion is the *Enhanced* SC 2.4.12,
  which we mark as out-of-scope.

The orchestrator hands us:
  - `tab_stops`: list of {selector, bbox: {x, y, w, h}, ...}
  - `page`:      live Playwright page so we can re-probe overlays

Rules emitted:

- `focus-obscured-by-sticky`  WCAG 2.4.11  serious  sticky overlay covers focused element
- `focus-obscured-by-fixed`   WCAG 2.4.11  serious  fixed overlay covers focused element
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# JS to enumerate fixed / sticky overlays at the time of the audit.
# We capture each one's current bounding rect — the orchestrator
# already restored the viewport before this probe, so coordinates
# are in the same frame as the keyboard module's tab_stops.
_OVERLAYS_JS = r"""
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
    const overlays = [];
    // Walk every element. Slightly expensive but the page is already
    // loaded and we cap collection at 30. The selector
    // `*` here outperforms typed selectors because we have to read
    // computed style anyway.
    const all = document.body ? document.body.querySelectorAll('*') : [];
    for (const el of all) {
        const s = getComputedStyle(el);
        const pos = s.position;
        if (pos !== 'fixed' && pos !== 'sticky') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        if (s.display === 'none' || s.visibility === 'hidden') continue;
        if (parseFloat(s.opacity) === 0) continue;
        // Background must be opaque-ish. A transparent fixed overlay
        // doesn't visually obscure (e.g. a position:fixed wrapper for
        // ESC-key listening). Heuristic: `background-color` not
        // rgba(...,0).
        const bg = s.backgroundColor || '';
        const transparent = /rgba?\([^,]+,[^,]+,[^,]+,\s*0\s*\)/.test(bg)
            || bg === 'transparent' || bg === '';
        if (transparent && parseInt(s.zIndex || '0') < 1) continue;
        overlays.push({
            selector: cssPath(el),
            position: pos,
            x: r.left, y: r.top, w: r.width, h: r.height,
            z_index: s.zIndex,
            html: (el.outerHTML || '').slice(0, 200),
        });
        if (overlays.length >= 30) break;
    }
    return overlays;
}
"""


def _bbox(stop: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Extract (x,y,w,h) from a keyboard tab-stop record. Returns
    None when the stop didn't carry a rect (older keyboard records,
    or screen-reader-only elements with zero geometry)."""
    box = stop.get("bbox") or stop.get("rect") or {}
    try:
        return (
            float(box.get("x", 0)),
            float(box.get("y", 0)),
            float(box.get("w") or box.get("width") or 0),
            float(box.get("h") or box.get("height") or 0),
        )
    except (TypeError, ValueError):
        return None


def _covers(overlay: dict[str, Any], stop_box: tuple[float, float, float, float]) -> bool:
    """Return True when `overlay` fully covers `stop_box` (the SC's
    'entirely hidden' threshold). Tolerance of 1px on each side
    handles browser rounding."""
    sx, sy, sw, sh = stop_box
    if sw <= 0 or sh <= 0:
        return False
    return (
        overlay["x"] - 1 <= sx
        and overlay["y"] - 1 <= sy
        and overlay["x"] + overlay["w"] + 1 >= sx + sw
        and overlay["y"] + overlay["h"] + 1 >= sy + sh
    )


def analyze(
    tab_stops: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for stop in tab_stops:
        sb = _bbox(stop)
        if not sb:
            continue
        for ov in overlays:
            # The overlay can cover itself or its descendants; that's
            # not a 2.4.11 finding (focusing a button inside the modal
            # doesn't make the modal "obscure" the button). Skip when
            # the focused selector lives under the overlay's selector.
            stop_sel = stop.get("selector", "")
            ov_sel = ov.get("selector", "")
            if stop_sel == ov_sel:
                continue
            # Approximate ancestor check: when stop's selector starts
            # with overlay's id-based selector (e.g. `#modal > .body
            # button`), the focus is inside the overlay.
            if ov_sel and ov_sel.startswith("#") and stop_sel.startswith(ov_sel):
                continue

            if not _covers(ov, sb):
                continue
            key = (stop_sel, ov_sel)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            rule_id = (
                "focus-obscured-by-sticky" if ov.get("position") == "sticky"
                else "focus-obscured-by-fixed"
            )
            issues.append(make_issue(
                issue_id=f"{rule_id}-{stop.get('selector', '?')}",
                module="focus_obscured",
                rule=rule_id,
                severity="serious",
                wcag=["2.4.11"],
                confidence="medium",
                title=(
                    f"Focused element fully covered by a {ov.get('position')}-"
                    "positioned overlay"
                ),
                description=(
                    "WCAG 2.4.11 (Focus Not Obscured, Minimum, AA, "
                    "new in 2.2) requires that no author-created "
                    "content fully hides the focused component. This "
                    f"focused element is entirely within a "
                    f"{ov.get('position')}-positioned ancestor "
                    "(typically a sticky header, cookie banner, or "
                    "chat widget) at the moment it received focus. "
                    "Heuristic — confidence is medium because we "
                    "cannot tell from a static snapshot whether the "
                    "page scrolls the focus into view a moment later."
                ),
                selector=stop.get("selector", ""),
                html_snippet=stop.get("html", "") or stop.get("html_snippet", ""),
                details={
                    "obscuring_selector": ov_sel,
                    "obscuring_position": ov.get("position"),
                    "obscuring_z_index": ov.get("z_index"),
                    "stop_box": list(sb),
                    "overlay_box": [ov["x"], ov["y"], ov["w"], ov["h"]],
                },
                fix=(
                    "Either ensure focus auto-scrolls the focused "
                    "element fully into view (`scrollMarginTop` set "
                    "to the sticky header height; CSS Scroll Padding "
                    "API), or refactor the sticky/fixed overlay so "
                    "it doesn't span the focusable area."
                ),
            ))
    return issues


def run(page, tab_stops: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    if not tab_stops:
        # Without a tab-stop list there's nothing to evaluate. The
        # orchestrator passes the keyboard module's stops in.
        return {
            "ran": True,
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
            "skipped_reason": "no tab_stops provided",
        }
    try:
        overlays = page.evaluate(_OVERLAYS_JS)
    except Exception as exc:
        log.exception("focus_obscured probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(tab_stops or [], overlays or [])
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "overlay_count": len(overlays or []),
        "stops_checked": len(tab_stops),
    }
