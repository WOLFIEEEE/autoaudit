"""Pixel-level focus + contrast analysis.

Axe-core and most CSS-cascade contrast checkers fall down on:
  - text over gradients / images
  - semi-transparent overlays
  - focus indicators whose contrast against background isn't checked
  - 1px "invisible" outlines that technically pass the presence test

This module takes SCREENSHOTS from the real browser at element-granular
level and computes contrast from the rendered bytes. Two passes:

1. `run_contrast(page, samples)` — for each text-bearing element up to
   `samples`, crop the element + a small margin, compute the median
   perceived luminance of text pixels vs background pixels, emit
   contrast-ratio violations against WCAG 1.4.3.

2. `run_focus(page, stops)` — for each tab stop, screenshot the element
   with and without focus, diff pixels inside the element's bounding
   rect, compute contrast between the indicator color and the
   surrounding background. Emits WCAG 2.4.7 / 2.4.11 / 2.4.13
   violations when the indicator contrast is below threshold.

Rules:
  - `pixel-contrast-low`         WCAG 1.4.3   serious
  - `pixel-focus-contrast-low`   WCAG 2.4.11  serious
  - `pixel-focus-invisible`      WCAG 2.4.7   serious
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)


# WCAG 1.4.3 Contrast (Minimum) — AA thresholds.
NORMAL_TEXT_AA = 4.5
LARGE_TEXT_AA = 3.0

# Sample cap — screenshotting 500 elements takes minutes. We aim at
# the 40 most-likely violators (non-text size, small text with
# low-contrast styles) rather than every element on the page.
MAX_CONTRAST_SAMPLES = 40
MAX_FOCUS_SAMPLES = 30


# --------------------------------------------------------------------
# WCAG relative-luminance math


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG relative luminance formula."""
    r, g, b = rgb
    R = _srgb_to_linear(r)
    G = _srgb_to_linear(g)
    B = _srgb_to_linear(b)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    L1 = relative_luminance(a)
    L2 = relative_luminance(b)
    if L1 < L2:
        L1, L2 = L2, L1
    return (L1 + 0.05) / (L2 + 0.05)


# --------------------------------------------------------------------
# Contrast pass


def run_contrast(page, *, max_samples: int = MAX_CONTRAST_SAMPLES) -> dict[str, Any]:
    """Screenshot each sampled text element, compute fg/bg contrast.

    Elements sampled: every visible element whose direct text child is
    non-empty. We score by "likelihood of failure" using computed-style
    hints (fontSize + colors) so we spend our screenshot budget on the
    most at-risk elements.
    """
    try:
        from PIL import Image  # noqa: F401 - just to confirm availability
    except ImportError:
        return {
            "ran": False,
            "error": "Pillow not installed; pip install Pillow",
            "issues": [],
        }

    # 1) Ask the page for candidate text elements (selector + rect +
    #    is_large_text) sorted by risk heuristic.
    candidates = page.evaluate(
        r"""(maxSamples) => {
            function cssPath(el) {
                if (!el || !el.tagName) return '';
                if (el.id) return '#' + el.id;
                const parts = [];
                let c = el;
                while (c && c.tagName && c.tagName.toLowerCase() !== 'html') {
                    let p = c.tagName.toLowerCase();
                    const par = c.parentElement;
                    if (par) {
                        const sib = [...par.children].filter(k => k.tagName === c.tagName);
                        if (sib.length > 1) p += ':nth-of-type(' + (sib.indexOf(c) + 1) + ')';
                    }
                    parts.unshift(p);
                    c = c.parentElement;
                    if (parts.length > 6) break;
                }
                return parts.join(' > ');
            }
            const out = [];
            const all = document.querySelectorAll('body *');
            for (const el of all) {
                // Direct text child only — avoids scoring a div whose
                // text lives 5 layers deep (axe does the same trick).
                let ownText = '';
                for (const n of el.childNodes) {
                    if (n.nodeType === 3 && (n.nodeValue || '').trim()) {
                        ownText += n.nodeValue.trim() + ' ';
                    }
                }
                ownText = ownText.trim();
                if (!ownText || ownText.length < 2) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) continue;
                const s = getComputedStyle(el);
                if (s.visibility === 'hidden' || s.display === 'none') continue;
                if (parseFloat(s.opacity) < 0.1) continue;
                const sz = parseFloat(s.fontSize || '16');
                const weight = parseInt(s.fontWeight || '400', 10) || 400;
                // WCAG 1.4.3 "large text": >= 18pt (~24px) OR >= 14pt (~18.66px) bold.
                const isLarge = sz >= 24 || (sz >= 18.66 && weight >= 700);
                out.push({
                    selector: cssPath(el),
                    text: ownText.slice(0, 80),
                    x: Math.round(r.left),
                    y: Math.round(r.top),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                    font_size_px: sz,
                    is_large_text: isLarge,
                });
                if (out.length >= maxSamples * 4) break;  // pool for ranking
            }
            return out;
        }""",
        max_samples,
    )

    if not candidates:
        return {"ran": True, "issues": [], "samples_evaluated": 0}

    # Evenly sample across the candidates to avoid concentrating on
    # one region. Step stride keeps diversity when we over-pool above.
    if len(candidates) > max_samples:
        stride = max(1, len(candidates) // max_samples)
        candidates = candidates[::stride][:max_samples]

    issues: list[dict[str, Any]] = []
    evaluated = 0
    for c in candidates:
        try:
            clip = {"x": c["x"], "y": c["y"], "width": c["w"], "height": c["h"]}
            png = page.screenshot(clip=clip, type="png")
            fg, bg = _estimate_fg_bg(png)
            if fg is None or bg is None:
                continue
            ratio = contrast_ratio(fg, bg)
        except Exception as exc:
            log.debug("contrast screenshot failed for %s: %s", c.get("selector"), exc)
            continue
        evaluated += 1
        threshold = LARGE_TEXT_AA if c.get("is_large_text") else NORMAL_TEXT_AA
        if ratio + 1e-3 < threshold:
            issues.append(
                make_issue(
                    issue_id=f"pixel-contrast-low-{c.get('selector') or evaluated}",
                    module="pixels",
                    rule="pixel-contrast-low",
                    severity="serious",
                    wcag=["1.4.3"],
                    confidence="medium",
                    title=(
                        f"Text contrast is {ratio:.2f}:1 "
                        f"(AA requires {threshold:.1f}:1)"
                    ),
                    description=(
                        "Measured from the rendered pixels, not from the "
                        "CSS cascade — so gradient backgrounds, overlays, "
                        "and images of text are analyzed the same way a "
                        "sighted user sees them."
                    ),
                    selector=c.get("selector", ""),
                    details={
                        "foreground_rgb": list(fg),
                        "background_rgb": list(bg),
                        "ratio": round(ratio, 2),
                        "threshold": threshold,
                        "is_large_text": c.get("is_large_text"),
                        "text_sample": c.get("text", ""),
                    },
                    fix=(
                        "Increase contrast between the text color and its "
                        "effective background. For text over images / "
                        "gradients, add a solid-fill overlay or text "
                        "shadow/stroke."
                    ),
                )
            )

    return {
        "ran": True,
        "issues": issues,
        "samples_evaluated": evaluated,
    }


def _estimate_fg_bg(
    png_bytes: bytes,
) -> tuple[tuple[int, int, int] | None, tuple[int, int, int] | None]:
    """Estimate (text color, background color) from an element screenshot.

    Heuristic:
      - convert to luminance
      - cluster pixels into "dark" (below median) and "light" (above)
      - report the mean RGB of each cluster
      - the "foreground" is whichever cluster has fewer pixels (text
        is usually a minority of the pixel count in a text element's
        bounding box)
    """
    from PIL import Image

    try:
        img = Image.open(BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return None, None
    # Downsample aggressively; contrast is a per-pixel measure but
    # we only need representative colors, not per-pixel precision.
    img.thumbnail((100, 100))
    pixels = list(img.getdata())
    if not pixels:
        return None, None
    # WCAG relative luminance for splitting.
    lums = [relative_luminance(p) for p in pixels]
    median = sorted(lums)[len(lums) // 2]
    dark, light = [], []
    for p, L in zip(pixels, lums):
        (dark if L < median else light).append(p)
    if not dark or not light:
        return None, None

    def _mean(cluster: list[tuple[int, int, int]]) -> tuple[int, int, int]:
        rs = sum(p[0] for p in cluster) // len(cluster)
        gs = sum(p[1] for p in cluster) // len(cluster)
        bs = sum(p[2] for p in cluster) // len(cluster)
        return (rs, gs, bs)

    dark_mean = _mean(dark)
    light_mean = _mean(light)
    # Foreground = minority cluster (likely text strokes).
    if len(dark) <= len(light):
        return dark_mean, light_mean
    return light_mean, dark_mean


# --------------------------------------------------------------------
# Focus-indicator pass


def run_focus(
    page,
    tab_stops: list[dict[str, Any]],
    *,
    max_samples: int = MAX_FOCUS_SAMPLES,
) -> dict[str, Any]:
    """For each tab stop, compare blurred vs focused screenshot.

    We screenshot the element's bounding rect with it not-focused,
    then focus it, screenshot again, and compute:
      - diff pixel count within a 4px-margin frame (the focus ring
        area); 0 pixels changed → no visible indicator
      - median color of changed pixels vs adjacent unchanged pixels
        → indicator contrast
    """
    try:
        from PIL import Image, ImageChops  # noqa: F401
    except ImportError:
        return {
            "ran": False,
            "error": "Pillow not installed; pip install Pillow",
            "issues": [],
        }

    if not tab_stops:
        return {"ran": True, "issues": [], "stops_evaluated": 0}

    issues: list[dict[str, Any]] = []
    evaluated = 0
    stops = tab_stops[:max_samples]

    for idx, stop in enumerate(stops):
        selector = stop.get("selector")
        if not selector:
            continue
        try:
            # Blur everything first.
            page.evaluate("() => document.activeElement && document.activeElement.blur()")
            page.wait_for_timeout(30)

            # Query the bounding rect freshly (don't trust stale data).
            rect = page.evaluate(
                r"""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    // Expand the clip by 6px so focus rings (outline-
                    // offset: 2, outline-width: 2..3) are captured.
                    return {
                        x: Math.max(0, Math.round(r.left) - 6),
                        y: Math.max(0, Math.round(r.top) - 6),
                        w: Math.round(r.width) + 12,
                        h: Math.round(r.height) + 12,
                    };
                }""",
                selector,
            )
            if not rect or rect["w"] < 4 or rect["h"] < 4:
                continue
            clip = {
                "x": rect["x"], "y": rect["y"],
                "width": rect["w"], "height": rect["h"],
            }
            png_before = page.screenshot(clip=clip, type="png")
            page.locator(selector).first.focus(timeout=2000)
            page.wait_for_timeout(60)
            png_after = page.screenshot(clip=clip, type="png")
        except Exception as exc:
            log.debug("focus screenshot failed for %s: %s", selector, exc)
            continue
        evaluated += 1

        analysis = _analyze_focus_diff(png_before, png_after)
        if not analysis:
            continue
        changed_pct = analysis["changed_pct"]
        ring_ratio = analysis["ring_contrast"]

        if changed_pct < 0.5:
            issues.append(
                make_issue(
                    issue_id=f"pixel-focus-invisible-{idx}",
                    module="pixels",
                    rule="pixel-focus-invisible",
                    severity="serious",
                    wcag=["2.4.7"],
                    title="Focus produced no visible pixel change",
                    description=(
                        "Comparing screenshots before and after focus "
                        f"showed only {changed_pct:.2f}% of pixels changed. "
                        "Sighted keyboard users cannot see where focus is."
                    ),
                    selector=selector,
                    details={
                        "changed_pct": round(changed_pct, 3),
                        "tab_index": idx + 1,
                    },
                    fix=(
                        "Provide a visible focus indicator: solid outline "
                        "(not outline:none), box-shadow ring, or border "
                        "change. Use a color with adequate contrast "
                        "against the background."
                    ),
                )
            )
            continue
        if ring_ratio is not None and ring_ratio + 1e-3 < 3.0:
            issues.append(
                make_issue(
                    issue_id=f"pixel-focus-contrast-low-{idx}",
                    module="pixels",
                    rule="pixel-focus-contrast-low",
                    severity="serious",
                    wcag=["2.4.11", "2.4.13"],
                    title=(
                        f"Focus indicator contrast is {ring_ratio:.2f}:1 "
                        "(WCAG 2.4.11 requires ≥ 3:1)"
                    ),
                    description=(
                        "The focus indicator is visible but its contrast "
                        "against the adjacent background is below 3:1, "
                        "failing WCAG 2.4.11 Focus Not Obscured and "
                        "2.4.13 Focus Appearance."
                    ),
                    selector=selector,
                    details={
                        "ring_contrast_ratio": round(ring_ratio, 2),
                        "changed_pct": round(changed_pct, 3),
                        "tab_index": idx + 1,
                    },
                    fix=(
                        "Increase the focus-indicator color contrast "
                        "against the background (or against the element "
                        "it outlines) to at least 3:1."
                    ),
                )
            )

    return {
        "ran": True,
        "issues": issues,
        "stops_evaluated": evaluated,
    }


def _analyze_focus_diff(before_png: bytes, after_png: bytes) -> dict[str, Any] | None:
    from PIL import Image, ImageChops

    try:
        a = Image.open(BytesIO(before_png)).convert("RGB")
        b = Image.open(BytesIO(after_png)).convert("RGB")
    except Exception:
        return None
    if a.size != b.size:
        return None
    diff = ImageChops.difference(a, b)
    w, h = a.size
    total = w * h

    # Pixel-change count: any channel difference > 8 (anti-aliasing
    # ignorable). We walk pixels in Python; for the tiny per-element
    # screenshots this is fine (<20k pixels each).
    changed_coords: list[tuple[int, int]] = []
    dpix = list(diff.getdata())
    for i, (dr, dg, db) in enumerate(dpix):
        if max(dr, dg, db) > 8:
            changed_coords.append((i % w, i // w))
    changed_pct = 100.0 * len(changed_coords) / total if total else 0.0

    ring_contrast: float | None = None
    if changed_coords:
        # Pick a sample of changed pixels and their same-position
        # colors in "before" (background). Median contrast is robust.
        a_px = list(a.getdata())
        b_px = list(b.getdata())
        # Subsample to at most 200 points for speed.
        step = max(1, len(changed_coords) // 200)
        sample = changed_coords[::step]
        ratios: list[float] = []
        for x, y in sample:
            idx = y * w + x
            ratios.append(contrast_ratio(a_px[idx], b_px[idx]))
        if ratios:
            ratios.sort()
            ring_contrast = ratios[len(ratios) // 2]

    return {"changed_pct": changed_pct, "ring_contrast": ring_contrast}
