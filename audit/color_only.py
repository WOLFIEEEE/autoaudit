"""Color-as-only-signifier heuristic — WCAG 1.4.1 (A).

WCAG 1.4.1 says: "Color is not used as the only visual means of
conveying information, indicating an action, prompting a response, or
distinguishing a visual element."

Full automation of this is impossible — it requires understanding the
*author's intent*. But we can detect a useful subset of failure
patterns with high precision:

  1. **Color-only inline error markers**: a span/em/etc. with a red
     foreground color, no error icon, no `*`, no `(required)` text,
     and an accessible name that doesn't itself describe the error.
     Common in form validation: "Email" labelled in red without the
     word "required" or an asterisk.

  2. **Status pills/badges with color but no text**: small elements
     where the only differentiator from peers is `background-color`,
     and accessible name is empty or matches the color name.

  3. **Links indistinguishable from surrounding text without color**
     (1.4.1 + 1.4.13). Inline link inside paragraph body that has
     `text-decoration: none` and only differs by `color`.

We bias hard toward precision: every issue is `confidence: low` (the
heuristic catches author intent imperfectly) so a reviewer can
acknowledge or dismiss without noise.

Rules emitted:

- `color-only-inline-marker`     WCAG 1.4.1  moderate  text-only "error" / "warning" inline span colored without other cue
- `color-only-link`              WCAG 1.4.1  moderate  link inside paragraph relies only on color

The analyzer is a pure function over a JS-extracted DOM snapshot, so
unit tests don't need a browser.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Foreground RGB triples that are commonly used for "error" / "warning"
# coloring. We compare hue-bucket rather than exact value because
# designers tweak shades constantly. The bucket is deliberately wide
# to keep false negatives manageable; the rule's low confidence
# documents the inevitable false positives.
def _is_warning_red(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 180 and g <= 100 and b <= 100


def _is_warning_amber(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 180 and 100 <= g <= 200 and b <= 100


def _is_success_green(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r <= 100 and g >= 140 and b <= 120


# Cues that, if present alongside a colored span, mean color is *not*
# the only signifier. Lowercased text-content match is the simplest
# detector that covers most real-world authoring.
_NON_COLOR_CUES = (
    "*", "(required)", "required",
    "warning", "error", "invalid", "success", "passed",
    "ok", "fail", "failed", "danger", "alert",
    # Icon-only signal: a font-icon class or SVG inside the span
    # also counts as a non-color cue. The probe surfaces this as
    # `has_icon_sibling` / `has_icon_child`.
)

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
    function parseRgb(s) {
        const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(s);
        if (!m) return null;
        return [Number(m[1]), Number(m[2]), Number(m[3])];
    }
    function hasIcon(el) {
        if (el.querySelector && (el.querySelector('svg, i, [class*="icon"], [class*="fa-"]'))) return true;
        return false;
    }

    // Pattern 1: Inline text spans (not whole-paragraph) with a red /
    // amber / green foreground that's distinct from their parent's
    // color. Cap candidates to avoid scanning ten thousand spans on
    // marketing pages.
    const inline = [];
    const spans = document.querySelectorAll(
        'span, em, strong, b, i, u, label, code, mark'
    );
    let scanned = 0;
    for (const el of spans) {
        if (scanned >= 400) break;
        scanned += 1;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        if (parseFloat(cs.opacity) === 0) continue;
        const myFg = parseRgb(cs.color || '');
        if (!myFg) continue;
        const parent = el.parentElement;
        if (!parent) continue;
        const parentFg = parseRgb(getComputedStyle(parent).color || '');
        if (!parentFg) continue;
        const sameColor = (
            myFg[0] === parentFg[0] && myFg[1] === parentFg[1] && myFg[2] === parentFg[2]
        );
        if (sameColor) continue;
        // Only spans with substantive text — pure color decoration of
        // empty <span> elements is reported by other rules.
        const text = (el.textContent || '').trim();
        if (text.length < 2 || text.length > 80) continue;

        inline.push({
            tag: el.tagName.toLowerCase(),
            text: text.slice(0, 80),
            text_lower: text.toLowerCase(),
            color: cs.color,
            rgb: myFg,
            parent_text: (parent.textContent || '').trim().slice(0, 200),
            has_icon_child: hasIcon(el),
            has_icon_sibling: !!(parent && parent.querySelector(
                ':scope > svg, :scope > i, :scope > [class*="icon"]'
            )),
            font_weight: cs.fontWeight,
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
        });
        if (inline.length >= 30) break;
    }

    // Pattern 2: links inside paragraphs that differ from surrounding
    // text only by color (no underline, no border, no weight change).
    const links = [];
    const anchors = document.querySelectorAll('p a[href], li a[href]');
    let aScanned = 0;
    for (const a of anchors) {
        if (aScanned >= 200) break;
        aScanned += 1;
        const cs = getComputedStyle(a);
        // Decoration: text-decoration-line includes "underline" if
        // the link is underlined; "none" means decoration is removed
        // and we need another cue.
        const decoration = (cs.textDecorationLine || cs.textDecoration || '').toLowerCase();
        const hasUnderline = decoration.includes('underline');
        const hasBorder = (cs.borderBottomStyle || '') !== 'none' && parseFloat(cs.borderBottomWidth) > 0;
        if (hasUnderline || hasBorder) continue;
        // Compare weight + style with parent text.
        const parent = a.parentElement;
        if (!parent) continue;
        const ps = getComputedStyle(parent);
        if (cs.fontWeight === ps.fontWeight && cs.fontStyle === ps.fontStyle) {
            const txt = (a.textContent || '').trim();
            if (!txt) continue;
            links.push({
                text: txt.slice(0, 80),
                color: cs.color,
                parent_color: ps.color,
                selector: cssPath(a),
                html: (a.outerHTML || '').slice(0, 200),
            });
            if (links.length >= 20) break;
        }
    }

    return {inline_markers: inline, color_only_links: links};
}
"""


def _has_non_color_cue(item: dict[str, Any]) -> bool:
    """Return True when something besides color disambiguates the marker."""
    if item.get("has_icon_child") or item.get("has_icon_sibling"):
        return True
    text_lower = (item.get("text_lower") or "").lower()
    parent_text = (item.get("parent_text") or "").lower()
    for cue in _NON_COLOR_CUES:
        if cue in text_lower or cue in parent_text:
            return True
    # Bold weight is itself a non-color cue (some browsers compute
    # 700 as "bold"; numeric 600+ also reads bold to most users).
    fw = item.get("font_weight") or ""
    try:
        if int(fw) >= 600:
            return True
    except (TypeError, ValueError):
        if fw.lower() in ("bold", "bolder"):
            return True
    return False


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for idx, item in enumerate(probe.get("inline_markers") or []):
        rgb = tuple(item.get("rgb") or (0, 0, 0))
        # Only fire when the color is one of the conventional
        # warning palettes — random green text in marketing copy
        # isn't a 1.4.1 violation.
        if not (_is_warning_red(rgb) or _is_warning_amber(rgb) or _is_success_green(rgb)):
            continue
        if _has_non_color_cue(item):
            continue
        kind = (
            "error" if _is_warning_red(rgb)
            else "warning" if _is_warning_amber(rgb)
            else "success"
        )
        issues.append(make_issue(
            issue_id=f"color-only-inline-marker-{idx}",
            module="color_only",
            rule="color-only-inline-marker",
            severity="moderate",
            wcag=["1.4.1"],
            confidence="low",
            title=(
                f"Inline {kind!r} marker may rely on color alone"
            ),
            description=(
                f"This element uses a {kind}-colored foreground "
                "different from its surrounding text, but contains "
                "no icon, asterisk, or wording (\"required\", "
                "\"error\", etc.) that would convey the meaning to a "
                "user who cannot perceive the color difference. WCAG "
                "1.4.1 (A) requires that color is not the only "
                "signifier. Heuristic detection — review and dismiss "
                "if a non-color cue is in fact present."
            ),
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            details={
                "text": item.get("text", ""),
                "color": item.get("color", ""),
                "kind": kind,
            },
            fix=(
                "Add a non-color signifier: an icon (e.g. ⚠ for "
                "warnings), an asterisk for required fields, the "
                "literal word \"error\"/\"required\", or a "
                "border/underline. Color may stay as a redundant cue."
            ),
        ))

    for idx, item in enumerate(probe.get("color_only_links") or []):
        # Same-color link → not actually a 1.4.1 risk because it's
        # not a link at all visually. Different-color but no other
        # cue is the failure mode.
        if (item.get("color") or "") == (item.get("parent_color") or ""):
            continue
        issues.append(make_issue(
            issue_id=f"color-only-link-{idx}",
            module="color_only",
            rule="color-only-link",
            severity="moderate",
            wcag=["1.4.1"],
            confidence="low",
            title=(
                "Inline link may be distinguishable only by color"
            ),
            description=(
                "This link sits inside body text with no underline, "
                "border, or weight change — only its color differs "
                "from the surrounding text. Users with color-vision "
                "deficiencies (red-green, monochromat, low contrast "
                "monitors) cannot tell it apart from non-link text. "
                "WCAG 1.4.1 (A) requires a non-color cue. Heuristic "
                "detection — false positives expected on links that "
                "have a hover-only underline (only the unhovered "
                "state is checked)."
            ),
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            details={
                "text": item.get("text", ""),
                "link_color": item.get("color", ""),
                "surrounding_color": item.get("parent_color", ""),
            },
            fix=(
                "Apply text-decoration: underline (most common), or "
                "add a border-bottom. If site style precludes "
                "underlines, ensure links have a different "
                "font-weight or a leading icon."
            ),
        ))

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("color_only probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "inline_marker_candidates": len((probe or {}).get("inline_markers") or []),
        "color_only_link_candidates": len((probe or {}).get("color_only_links") or []),
    }
