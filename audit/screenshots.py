"""Per-issue annotated screenshots.

Embeds a small screenshot of the offending element into each issue's
`details.screenshot` field as a data: URI. The HTML report template
renders the URI directly so reports are self-contained (no external
image files).

Marking is made deliberately clear so a non-technical stakeholder can
see exactly what failed:
  - a high-contrast highlight (white halo under a crimson box) that
    stays visible on both light and dark backgrounds, and
  - a small severity label chip pinned to the highlight.

Mobile-only findings (tagged `viewport: "mobile"`) are captured at a
phone viewport — otherwise the element is `display:none` at desktop
width and the screenshot would come back empty.

Kept opt-in via `options["screenshots"]=True` because screenshotting
every issue adds real audit time on pages with 100+ findings.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Any

log = logging.getLogger(__name__)

# Cap total screenshots per audit. A page with 200 issues will waste
# minutes capturing every one — past ~30 annotated thumbnails the
# report becomes unreadable anyway. We prefer the 30 highest-severity.
MAX_SCREENSHOTS = 30

# Max screenshots per rule. Without this, three repetitive rules (10x
# unnamed links, 10x low contrast) eat the entire budget and the report
# shows ten near-identical thumbnails while the carousel, the mobile
# menu, and every other distinct defect get none. Capping per-rule
# spreads the budget across issue *types* — far more useful to a
# stakeholder skimming the report.
PER_RULE_SCREENSHOTS = 3

# Capture margin around the element. Gives context (surrounding
# elements) so the user can orient themselves on the rendered page.
CAPTURE_MARGIN_PX = 24

# Phone viewport for mobile-tagged findings — matches the orchestrator's
# mobile pass so the element is laid out the same way it was detected.
MOBILE_VIEWPORT = {"width": 390, "height": 844}

_CRIMSON = (220, 20, 60)
_WHITE = (255, 255, 255)


def annotate_issues(
    page,
    issues: list[dict[str, Any]],
    *,
    max_shots: int = MAX_SCREENSHOTS,
) -> list[dict[str, Any]]:
    """Mutate `issues` by attaching details['screenshot'] (data URI).

    Returns the same list for chaining. Only high-priority issues get
    screenshots: critical + serious first, then moderate, until the
    budget is reached. Minor findings are skipped.

    Desktop findings are captured at the current viewport; mobile-tagged
    findings are captured at a phone viewport (then the viewport is
    restored) so their elements are actually visible.
    """
    # Pillow is an optional dependency. When the audit ran with
    # `screenshots: True` but Pillow isn't installed, we degrade
    # silently — issues come back without `details.screenshot`,
    # and the report template's data:image guard renders nothing.
    # Surface this at WARNING level so operators notice the gap
    # rather than wondering why their opt-in produced no images.
    try:
        from PIL import Image, ImageDraw  # noqa: F401 - import probe
    except ImportError:
        log.warning(
            "screenshots option enabled but Pillow is not installed; "
            "issues will not include `details.screenshot`. "
            "Install with: pip install Pillow"
        )
        return issues

    # Sort by severity + whether we have a selector.
    rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    prioritized = sorted(
        [i for i in issues if (i.get("element") or {}).get("selector")],
        key=lambda i: rank.get(i.get("severity", "minor"), 9),
    )
    desktop = [i for i in prioritized if i.get("viewport") != "mobile"]
    mobile = [i for i in prioritized if i.get("viewport") == "mobile"]

    # Per-rule tally shared across both batches so a rule can't exceed
    # PER_RULE_SCREENSHOTS whether its instances are desktop or mobile.
    per_rule: dict[str, int] = {}

    # Mobile-only findings are captured FIRST, against a reserved slice
    # of the budget. They're few (a hamburger, an off-canvas control) but
    # unique and high-value, and would otherwise be crowded out by a
    # high-issue desktop page. Requires resizing to a phone viewport so
    # the display:none-at-desktop element actually renders.
    budget = 0
    if mobile:
        mobile_cap = min(len(mobile), max(1, max_shots // 3))
        try:
            original_vp = page.viewport_size or {"width": 1280, "height": 720}
        except Exception:  # pragma: no cover - defensive
            original_vp = {"width": 1280, "height": 720}
        try:
            page.set_viewport_size(MOBILE_VIEWPORT)
            page.wait_for_timeout(300)
            budget = _shoot_batch(page, mobile, mobile_cap, 0, per_rule)
        except Exception:
            log.debug("mobile screenshot pass failed", exc_info=True)
        finally:
            try:
                page.set_viewport_size(original_vp)
                page.wait_for_timeout(100)
            except Exception:  # pragma: no cover - defensive
                pass

    _shoot_batch(page, desktop, max_shots, budget, per_rule)
    return issues


def _shoot_batch(
    page,
    issues: list[dict[str, Any]],
    max_shots: int,
    budget: int,
    per_rule: dict[str, int],
) -> int:
    """Screenshot each issue until the budget is spent or PER_RULE cap is
    hit for its rule. Returns the updated budget count."""
    for issue in issues:
        if budget >= max_shots:
            break
        rule = issue.get("rule", "")
        if per_rule.get(rule, 0) >= PER_RULE_SCREENSHOTS:
            continue
        selector = (issue.get("element") or {}).get("selector")
        if not selector:
            continue
        label = (issue.get("severity") or "issue").upper()
        try:
            data_uri = _shoot(page, selector, label=label)
        except Exception as exc:
            log.debug("screenshot failed for %s: %s", selector, exc)
            continue
        if not data_uri:
            continue
        issue.setdefault("details", {})["screenshot"] = data_uri
        per_rule[rule] = per_rule.get(rule, 0) + 1
        budget += 1
    return budget


def _draw_label(draw, x: int, y_top: int, text: str) -> None:
    """Draw a filled crimson chip with white text near (x, y_top).

    Placed just above the highlight when there's room, otherwise tucked
    inside its top-left corner. Uses PIL's default bitmap font so no
    font file is required.
    """
    from PIL import ImageFont

    try:
        font = ImageFont.load_default()
    except Exception:  # pragma: no cover - defensive
        font = None
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:  # pragma: no cover - very old Pillow
        tw, th = 6 * len(text), 11
    pad = 3
    chip_w, chip_h = tw + 2 * pad, th + 2 * pad
    lx = max(0, x)
    ly = y_top - chip_h
    if ly < 0:  # no room above → tuck inside
        ly = max(0, y_top)
    draw.rectangle([lx, ly, lx + chip_w, ly + chip_h], fill=_CRIMSON)
    draw.text((lx + pad, ly + pad - 1), text, fill=_WHITE, font=font)


def _shoot(page, selector: str, *, label: str = "") -> str | None:
    """Capture selector + its immediate surroundings, draw a clear
    high-contrast box + severity chip around the target, return a
    base64 data URI."""
    from PIL import Image, ImageDraw

    rect = page.evaluate(
        r"""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
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

    vw, vh = int(rect["vw"]), int(rect["vh"])
    # Clip to the viewport, padded with CAPTURE_MARGIN_PX. Playwright
    # will reject clip coordinates that fall outside the viewport, so
    # we clamp.
    x0 = max(0, int(rect["x"]) - CAPTURE_MARGIN_PX)
    y0 = max(0, int(rect["y"]) - CAPTURE_MARGIN_PX)
    x1 = min(vw, int(rect["x"] + rect["w"]) + CAPTURE_MARGIN_PX)
    y1 = min(vh, int(rect["y"] + rect["h"]) + CAPTURE_MARGIN_PX)
    clip_w, clip_h = max(1, x1 - x0), max(1, y1 - y0)

    png = page.screenshot(
        clip={"x": x0, "y": y0, "width": clip_w, "height": clip_h},
        type="png",
    )
    img = Image.open(BytesIO(png)).convert("RGB")

    # Scale to a reasonable thumbnail (keeps data URI size small).
    max_w = 800
    if img.width > max_w:
        scale = max_w / img.width
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)),
            Image.LANCZOS,
        )
    scale_x = img.width / clip_w
    scale_y = img.height / clip_h

    # Highlight rectangle in the scaled coordinate space.
    rx0 = int((rect["x"] - x0) * scale_x)
    ry0 = int((rect["y"] - y0) * scale_y)
    rx1 = int((rect["x"] + rect["w"] - x0) * scale_x)
    ry1 = int((rect["y"] + rect["h"] - y0) * scale_y)
    draw = ImageDraw.Draw(img)

    # High-contrast double border: a white halo underneath so the box is
    # visible on dark elements, crimson on top so it reads as "error".
    for t in range(6, 3, -1):
        draw.rectangle([rx0 - t, ry0 - t, rx1 + t, ry1 + t], outline=_WHITE)
    for t in range(0, 3):
        draw.rectangle([rx0 - t, ry0 - t, rx1 + t, ry1 + t], outline=_CRIMSON)

    if label:
        _draw_label(draw, rx0 - 3, ry0 - 6, label)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
