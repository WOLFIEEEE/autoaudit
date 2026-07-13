"""Mobile-specific SC coverage.

WCAG success criteria that only manifest on small-screen / touch
contexts are poorly covered by generic DOM analysis. This module:

1. Switches the viewport to a typical phone (iPhone SE / 375×667)
2. Re-checks the DOM for patterns that only fail at that size
3. Looks for touch-specific affordances that don't have keyboard/
   single-pointer alternatives (2.5.1) or that rely on drag (2.5.7)

Rules emitted:
  - `mobile-orientation-locked`    WCAG 1.3.4  serious   CSS/JS locks orientation
  - `mobile-pointer-gesture`       WCAG 2.5.1  moderate  element uses multi-finger / path gesture without alt
  - `mobile-drag-only`             WCAG 2.5.7  moderate  draggable with no click/keyboard alternative
  - `mobile-motion-actuation`      WCAG 2.5.4  minor     JS reads DeviceMotion without opt-out
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue
from audit._js_helpers import CSS_PATH_JS

log = logging.getLogger(__name__)

# Phone-ish dimensions. iPhone SE is a common small-target size.
PHONE_WIDTH = 375
PHONE_HEIGHT = 667

_PROBE_JS = "() => {\n" + CSS_PATH_JS + "\n" + r"""
    // 1.3.4 Orientation: look for CSS that forces a single orientation
    //   - <meta name="viewport" content="..."> with orientation=
    //   - screen.orientation.lock() call signatures in inline scripts
    // We can't inspect <script> contents reliably; flag the meta tag
    // case (which is the common static one).
    const viewport_meta = document.querySelector('meta[name="viewport"]');
    const vp_content = viewport_meta ? (viewport_meta.getAttribute('content') || '') : '';
    const orientation_locked = /orientation\s*=\s*(portrait|landscape)/i.test(vp_content);

    // 2.5.7 Dragging movements: elements with draggable=true but
    //   whose nearest ancestor doesn't also respond to click or
    //   provide a keyboard alternative.
    const drag_only = [];
    for (const el of [...document.querySelectorAll('[draggable="true"]')]) {
        // Strong signal: the element also listens for keyboard or
        // click-based alternatives. We can't see JS listeners from
        // the outside, but the PRESENCE of a role/aria-keyshortcuts
        // suggests the author thought about alternatives.
        const hasKb = !!(
            el.getAttribute('role') === 'button' ||
            el.getAttribute('aria-keyshortcuts') ||
            el.querySelector('button, a[href]')
        );
        if (hasKb) continue;
        drag_only.push({
            selector: cssPath(el),
            tag: el.tagName.toLowerCase(),
            html: el.outerHTML.slice(0, 200),
        });
    }

    // 2.5.1 Pointer Gestures: we look for touch-event handlers on
    // elements that expose gestures. Inline handlers (ontouchstart=,
    // ontouchmove=) are what we can read from attributes; JS-attached
    // handlers are opaque. That's a gap we openly document.
    const gesture_suspects = [];
    for (const el of [...document.querySelectorAll('[ontouchstart], [ontouchmove], [ontouchend]')]) {
        gesture_suspects.push({
            selector: cssPath(el),
            tag: el.tagName.toLowerCase(),
            html: el.outerHTML.slice(0, 200),
        });
    }

    // 2.5.4 Motion Actuation: look for inline handlers that
    // reference devicemotion / deviceorientation events.
    const motion_actuation = [];
    // Can inspect inline scripts' text for these APIs.
    for (const s of [...document.querySelectorAll('script:not([src])')]) {
        const txt = s.textContent || '';
        if (/\b(DeviceMotionEvent|DeviceOrientationEvent|devicemotion|deviceorientation)\b/.test(txt)) {
            motion_actuation.push({
                selector: cssPath(s),
                preview: txt.slice(0, 160),
            });
        }
    }

    return {
        orientation_locked,
        viewport_content: vp_content,
        drag_only,
        gesture_suspects,
        motion_actuation,
    };
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    # Remember original viewport so we can restore — mobile mode must
    # NOT leak into later modules (same policy as reflow.py).
    try:
        original = page.viewport_size or {"width": 1280, "height": 720}
    except Exception as exc:
        log.warning(
            "page.viewport_size raised %s: %s; using default 1280x720",
            type(exc).__name__,
            exc,
        )
        original = {"width": 1280, "height": 720}

    issues: list[dict[str, Any]] = []
    try:
        page.set_viewport_size({"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
        page.wait_for_timeout(300)
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("mobile probe failed")
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    finally:
        try:
            page.set_viewport_size(original)
            page.wait_for_timeout(200)
        except Exception as exc:
            log.debug("mobile viewport restore failed: %s", exc)

    if probe.get("orientation_locked"):
        issues.append(
            make_issue(
                issue_id="mobile-orientation-locked",
                module="mobile",
                rule="mobile-orientation-locked",
                severity="serious",
                wcag=["1.3.4"],
                title="Viewport meta tag locks screen orientation",
                description=(
                    "The <meta name=\"viewport\"> tag contains an "
                    "`orientation=` directive, which forces the page "
                    "into portrait or landscape. Users mounting a "
                    "device in the opposite orientation (wheelchair "
                    "mounts, sight-line assistants) cannot use the page."
                ),
                selector='meta[name="viewport"]',
                details={"viewport_content": probe.get("viewport_content", "")},
                fix=(
                    "Remove the orientation= directive. If layout "
                    "issues appear in the other orientation, fix them "
                    "with responsive CSS rather than locking."
                ),
            )
        )

    for idx, d in enumerate(probe.get("drag_only") or []):
        issues.append(
            make_issue(
                issue_id=f"mobile-drag-only-{idx}",
                module="mobile",
                rule="mobile-drag-only",
                severity="moderate",
                wcag=["2.5.7"],
                confidence="medium",
                title="Element is draggable with no visible alternative",
                description=(
                    "This element uses draggable=true but we did not "
                    "detect an obvious click / keyboard alternative. "
                    "Users who cannot drag (motor impairments, some "
                    "SR users) may be locked out of this interaction."
                ),
                selector=d.get("selector", ""),
                html_snippet=d.get("html", ""),
                fix=(
                    "Provide an equivalent way to perform the same "
                    "action — typically buttons that move / reorder / "
                    "submit, reachable by keyboard and single-pointer."
                ),
            )
        )

    for idx, g in enumerate(probe.get("gesture_suspects") or []):
        issues.append(
            make_issue(
                issue_id=f"mobile-pointer-gesture-{idx}",
                module="mobile",
                rule="mobile-pointer-gesture",
                severity="moderate",
                wcag=["2.5.1"],
                confidence="low",
                title="Element has inline touch handlers — verify gesture alternatives",
                description=(
                    "Inline touch handlers (ontouchstart/-move/-end) "
                    "on this element suggest a custom gesture. If the "
                    "gesture requires multiple fingers or a path "
                    "(swipe/pinch), WCAG 2.5.1 (A) requires a single-"
                    "pointer alternative. We can't see the handler "
                    "body from outside, so this is a manual-review lead."
                ),
                selector=g.get("selector", ""),
                html_snippet=g.get("html", ""),
                fix=(
                    "Audit the gesture manually. If multi-touch or a "
                    "path is required, add a button-based alternative."
                ),
            )
        )

    for idx, m in enumerate(probe.get("motion_actuation") or []):
        issues.append(
            make_issue(
                issue_id=f"mobile-motion-actuation-{idx}",
                module="mobile",
                rule="mobile-motion-actuation",
                severity="moderate",
                wcag=["2.5.4"],
                confidence="medium",
                title="Page responds to device motion — verify opt-out",
                description=(
                    "An inline script references DeviceMotionEvent or "
                    "DeviceOrientationEvent. WCAG 2.5.4 (A) requires "
                    "motion-triggered actions to (a) have a UI "
                    "alternative and (b) be disableable by the user."
                ),
                selector=m.get("selector", ""),
                details={"script_preview": (m.get("preview") or "")[:160]},
                fix=(
                    "Expose a 'disable motion' control, AND provide a "
                    "conventional UI (buttons, menus) that does the "
                    "same thing without moving the device."
                ),
            )
        )

    return {
        "ran": True,
        "issues": issues,
        "probe": probe,
        "duration_seconds": round(time.time() - start, 3),
    }
