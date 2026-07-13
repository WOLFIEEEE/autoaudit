"""Reflow module — WCAG 1.4.10 (AA).

"Content can be presented without loss of information or functionality,
and without requiring scrolling in two dimensions, at a width equivalent
to 320 CSS pixels."

We resize the viewport to 320×256, wait for layout to settle, and then
probe for the three things that fail 1.4.10 in practice:

1. Horizontal scrollbar on the document (content too wide).
2. Any element with visible content extending beyond the 320px viewport
   (catches sticky headers, wide tables, fixed-width images).
3. `overflow-x: hidden` on the body or html that would hide overflowing
   content without telling the user — technically "no horizontal scroll"
   but with information loss, which is the stricter failure.

Rules emitted:
- `reflow-horizontal-scroll`     WCAG 1.4.10  serious
- `reflow-overflow-clipped`      WCAG 1.4.10  serious
- `reflow-element-exceeds`       WCAG 1.4.10  moderate
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# WCAG 1.4.10 specifies 320 CSS pixels wide. Height of 256 CSS pixels
# is the companion number in the SC (used for vertical-scrolling content
# like captions). Both are minima: we test the stricter case.
REFLOW_WIDTH = 320
REFLOW_HEIGHT = 256

_PROBE_JS = r"""
(target) => {
    const de = document.documentElement;
    const body = document.body;
    const docWidth = Math.max(de.scrollWidth, body ? body.scrollWidth : 0);
    const viewport = window.innerWidth;
    const horizScroll = docWidth > viewport + 1;  // 1px tolerance for rounding
    const htmlStyle = getComputedStyle(de);
    const bodyStyle = body ? getComputedStyle(body) : null;
    const clipped = (
        (htmlStyle.overflowX === 'hidden' && docWidth > viewport + 1) ||
        (bodyStyle && bodyStyle.overflowX === 'hidden' && docWidth > viewport + 1)
    );
    // Find up to 10 elements whose right edge extends past the viewport.
    // Skip invisible ones (display:none, zero-size). We also skip
    // position: fixed / sticky because those legitimately stay at
    // their original coordinates during reflow testing.
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
            if (parts.length > 5) break;
        }
        return parts.join(' > ');
    }
    const overflowing = [];
    const all = document.querySelectorAll('body *');
    for (const el of all) {
        if (overflowing.length >= 10) break;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') continue;
        if (s.position === 'fixed' || s.position === 'sticky') continue;
        if (r.right > target + 1) {
            overflowing.push({
                selector: cssPath(el),
                tag: el.tagName.toLowerCase(),
                right: Math.round(r.right),
                width: Math.round(r.width),
                html: el.outerHTML.slice(0, 160),
            });
        }
    }
    return {
        viewport_width: viewport,
        document_width: docWidth,
        horizontal_scroll: horizScroll,
        overflow_clipped: clipped,
        overflowing_elements: overflowing,
    };
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Resize the page to 320×256, probe for reflow failures, restore."""
    start = time.time()
    # Remember the original viewport so the rest of the audit isn't
    # affected by our shrink. Playwright's page.viewport_size may be
    # None if set_viewport_size was never called; fall back to a sane
    # default matching the AuditOptions default.
    try:
        original = page.viewport_size or {"width": 1280, "height": 720}
    except Exception:
        original = {"width": 1280, "height": 720}

    issues: list[dict[str, Any]] = []
    try:
        page.set_viewport_size({"width": REFLOW_WIDTH, "height": REFLOW_HEIGHT})
        # Give CSS media queries and layout time to re-run. 400ms is
        # a comfortable margin for typical SPAs without dragging out
        # the audit.
        page.wait_for_timeout(400)
        probe = page.evaluate(_PROBE_JS, REFLOW_WIDTH)
    except Exception as exc:
        log.exception("reflow probe failed")
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    finally:
        # Always restore the original viewport so downstream modules
        # don't see our shrink as "the" viewport.
        try:
            page.set_viewport_size(original)
            page.wait_for_timeout(200)
        except Exception as exc:  # pragma: no cover - best effort
            log.debug("viewport restore failed: %s", exc)

    doc_width = probe.get("document_width", 0)

    if probe.get("horizontal_scroll") and not probe.get("overflow_clipped"):
        issues.append(
            make_issue(
                issue_id="reflow-horizontal-scroll",
                module="reflow",
                rule="reflow-horizontal-scroll",
                severity="serious",
                wcag=["1.4.10"],
                title="Page requires horizontal scrolling at 320px viewport",
                description=(
                    f"At a 320 CSS-pixel viewport, the document is "
                    f"{doc_width}px wide, forcing users to scroll in two "
                    "dimensions. Users with low vision at 400% zoom or "
                    "mobile phones in portrait cannot read the content "
                    "without constant horizontal scrolling."
                ),
                details={
                    "document_width_px": doc_width,
                    "viewport_width_px": REFLOW_WIDTH,
                },
                fix=(
                    "Use responsive CSS (max-width: 100%, flexbox, grid, "
                    "media queries) so content wraps within the viewport. "
                    "Common culprits: fixed-width tables, untargeted "
                    "<pre>, wide images without max-width."
                ),
            )
        )

    if probe.get("overflow_clipped"):
        issues.append(
            make_issue(
                issue_id="reflow-overflow-clipped",
                module="reflow",
                rule="reflow-overflow-clipped",
                severity="serious",
                wcag=["1.4.10"],
                title="Content is clipped at 320px viewport (overflow-x hidden)",
                description=(
                    "At a 320 CSS-pixel viewport, the <html> or <body> "
                    "element sets overflow-x: hidden while content "
                    "extends beyond that width. Users cannot scroll "
                    "to see the clipped content — information is lost."
                ),
                details={
                    "document_width_px": doc_width,
                    "viewport_width_px": REFLOW_WIDTH,
                },
                fix=(
                    "Remove overflow-x: hidden from html / body and fix "
                    "the underlying layout so content fits at 320 CSS "
                    "pixels without scrolling in two dimensions."
                ),
            )
        )

    for idx, el in enumerate((probe.get("overflowing_elements") or [])):
        issues.append(
            make_issue(
                issue_id=f"reflow-element-exceeds-{idx}",
                module="reflow",
                rule="reflow-element-exceeds",
                severity="moderate",
                wcag=["1.4.10"],
                title=f"<{el.get('tag')}> extends past the 320px viewport",
                description=(
                    f"This element's right edge sits at {el.get('right')}px, "
                    f"beyond the 320px reflow minimum. Contributes to the "
                    "page-wide horizontal scrolling failure."
                ),
                selector=el.get("selector", ""),
                html_snippet=el.get("html", ""),
                details={
                    "right_px": el.get("right"),
                    "width_px": el.get("width"),
                },
                fix=(
                    "Set max-width: 100% on images, allow tables to "
                    "scroll inside a sized container, or stack layout "
                    "on narrow viewports via a media query."
                ),
            )
        )

    return {
        "ran": True,
        "issues": issues,
        "probe": probe,
        "duration_seconds": round(time.time() - start, 3),
    }
