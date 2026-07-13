"""Target Size (Minimum) module — WCAG 2.5.8 (AA, new in 2.2).

> "The size of the target for pointer inputs is at least 24 by 24 CSS
> pixels, except where: ..."

The SC carries five exceptions. We detect the two that are reliably
machine-checkable:

  - **Inline**  — the target is rendered inline within a sentence or
                  block of text. Tested by `display: inline*` AND a
                  text-bearing block ancestor.
  - **Spacing** — an undersized target has a 24-CSS-pixel diameter
                  circle of clear space centered on it (no other
                  pointer target intersects that circle).

The remaining exceptions need human judgement and are surfaced in the
fix text rather than detected:

  - **Equivalent**     — false negatives are likely if we guessed.
  - **User agent**     — only triggers when the page hasn't styled the
                         control at all; vanishingly rare on real sites.
  - **Essential**      — by definition not detectable.

Rules emitted:

- `target-size-undersized`   WCAG 2.5.8  serious  — < 24x24 CSS px without
                                                     a satisfying exception
- `target-size-spacing-tight` WCAG 2.5.8  moderate — undersized but spacing
                                                     exception currently applies;
                                                     fragile against layout drift
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

MIN_PX = 24

# Cap how many violations we report per audit. Real-world failures
# tend to cluster (one icon button repeated 50 times in a list), and
# the dedup pass collapses them by element fingerprint anyway. The cap
# is a defensive backstop for pathological pages with thousands of
# matches — the JS budget would otherwise dominate the audit.
MAX_REPORTED = 50

_PROBE_JS = r"""
(min) => {
    // Selectors for "pointer target" — anything a sighted user can
    // click/tap. We deliberately exclude inputs of type=hidden and
    // <input type=text/number/email/...> with computed display:none.
    const SEL = [
        'a[href]',
        'button',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        'summary',
        '[role="button"]',
        '[role="link"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="switch"]',
        '[role="menuitem"]',
        '[role="menuitemcheckbox"]',
        '[role="menuitemradio"]',
        '[role="tab"]',
        '[role="option"]',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

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

    function visible(el, r) {
        if (r.width < 1 || r.height < 1) return false;
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        if (parseFloat(s.opacity) === 0) return false;
        return true;
    }

    // "Inline" exception: the target is laid out inline AND its
    // nearest block-level ancestor contains substantive text content.
    // Pure icon-only links inside a flex container don't qualify
    // (their parent is not a "sentence or block of text").
    function isInline(el) {
        const s = getComputedStyle(el);
        if (!/^inline/.test(s.display)) return false;
        let cur = el.parentElement;
        let hops = 0;
        while (cur && hops < 5) {
            const ps = getComputedStyle(cur);
            if (ps.display === 'block' || /flow-root|list-item/.test(ps.display)) {
                // Heuristic: parent has > 20 chars of text outside of
                // its descendant interactive children. Empty wrappers
                // (e.g. nav <li>) won't qualify.
                const text = (cur.innerText || '').replace(/\s+/g, ' ').trim();
                return text.length > 20;
            }
            cur = cur.parentElement;
            hops += 1;
        }
        return false;
    }

    // Collect raw measurements first; spacing-exception eval needs
    // rectangles for siblings, not just the focused node.
    const all = Array.from(document.querySelectorAll(SEL));
    const rects = [];
    for (const el of all) {
        const r = el.getBoundingClientRect();
        if (!visible(el, r)) continue;
        rects.push({el, r});
    }

    // Indexed by selector for explanatory output. We allow up to N
    // findings; the cap is enforced Python-side after sorting.
    const findings = [];
    for (let i = 0; i < rects.length; i += 1) {
        const {el, r} = rects[i];
        const w = r.width;
        const h = r.height;
        if (w >= min && h >= min) continue;

        // Inline exception?
        if (isInline(el)) continue;

        // Spacing exception: a circle of diameter `min` centered on
        // the target's center has no other target's bounding rect
        // intersecting it (excluding self). Approximate with the
        // smallest side: the spacing required equals (min - side)/2
        // to satisfy the geometric "clear circle" interpretation.
        const cx = r.left + w / 2;
        const cy = r.top + h / 2;
        const radius = min / 2;
        let intrudes = false;
        for (let j = 0; j < rects.length; j += 1) {
            if (i === j) continue;
            const o = rects[j].r;
            // Closest point on `o` to (cx,cy):
            const ox = Math.max(o.left, Math.min(cx, o.right));
            const oy = Math.max(o.top,  Math.min(cy, o.bottom));
            const dx = cx - ox;
            const dy = cy - oy;
            if ((dx * dx + dy * dy) < radius * radius) {
                intrudes = true;
                break;
            }
        }

        const tag = el.tagName.toLowerCase();
        const html = (el.outerHTML || '').slice(0, 200);
        const accName = (
            el.getAttribute('aria-label')
            || (el.innerText || '').trim().slice(0, 80)
            || el.getAttribute('alt')
            || el.getAttribute('title')
            || ''
        );
        findings.push({
            selector: cssPath(el),
            tag,
            html_snippet: html,
            width: Math.round(w * 100) / 100,
            height: Math.round(h * 100) / 100,
            accessible_name: accName,
            spacing_exception_applies: !intrudes,
        });
    }

    // Page-wide spacing summary. Pairs of nearest interactive
    // controls; the minimum gap is a useful "how cramped is the UI"
    // signal even when no single target fails. We compare bounding
    // rects, not centers — touching siblings score 0.
    let min_gap = Infinity;
    let min_gap_pair = null;
    for (let i = 0; i < rects.length; i += 1) {
        const a = rects[i].r;
        for (let j = i + 1; j < rects.length; j += 1) {
            const b = rects[j].r;
            // Manhattan-style separation: how far the rects need to
            // travel toward each other to touch. 0 if they overlap.
            const dx = Math.max(0, Math.max(a.left, b.left) - Math.min(a.right, b.right));
            const dy = Math.max(0, Math.max(a.top, b.top) - Math.min(a.bottom, b.bottom));
            const gap = Math.sqrt(dx * dx + dy * dy);
            if (gap < min_gap) {
                min_gap = gap;
                min_gap_pair = {a: cssPath(rects[i].el), b: cssPath(rects[j].el)};
            }
        }
    }

    return {
        findings: findings,
        target_count: rects.length,
        min_gap_px: rects.length >= 2 ? Math.round(min_gap * 100) / 100 : null,
        min_gap_pair: min_gap_pair,
    };
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Probe the rendered DOM for sub-24×24 pointer targets."""
    start = time.time()
    issues: list[dict[str, Any]] = []
    try:
        probe = page.evaluate(_PROBE_JS, MIN_PX)
    except Exception as exc:
        log.exception("target_size probe failed")
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    # Backward compatibility: older probes returned a bare list. The
    # new shape is `{findings, target_count, min_gap_px, ...}`. Tests
    # that fake `evaluate` to return a list still work.
    if isinstance(probe, list):
        findings = probe
        target_count = len(findings)
        min_gap_px = None
        min_gap_pair = None
    else:
        findings = probe.get("findings") or []
        target_count = int(probe.get("target_count") or len(findings))
        min_gap_px = probe.get("min_gap_px")
        min_gap_pair = probe.get("min_gap_pair")

    # Sort: outright failures (no exception) first, then spacing-tight
    # ones; within each group, smallest-first so the worst offender
    # leads the report.
    def _rank(f: dict[str, Any]) -> tuple:
        return (
            0 if not f.get("spacing_exception_applies") else 1,
            min(float(f.get("width", 0)), float(f.get("height", 0))),
        )

    findings_sorted = sorted(findings, key=_rank)

    for idx, f in enumerate(findings_sorted[:MAX_REPORTED]):
        spacing_ok = bool(f.get("spacing_exception_applies"))
        rule_id = (
            "target-size-spacing-tight"
            if spacing_ok
            else "target-size-undersized"
        )
        severity = "moderate" if spacing_ok else "serious"
        name = f.get("accessible_name") or f"<{f.get('tag', '?')}>"
        title = (
            f"Target {name!r} is {f.get('width', 0):.0f}×{f.get('height', 0):.0f} "
            f"CSS px (below 24×24)"
        )
        if spacing_ok:
            description = (
                "This pointer target is below the 24 CSS-pixel minimum but "
                "currently satisfies WCAG 2.5.8's spacing exception (no "
                "other target sits inside a 24-pixel-diameter circle "
                "centered on it). The exception is fragile: any future "
                "layout change that brings another control nearby will "
                "convert this into a hard failure. Treat as a latent risk."
            )
            fix = (
                "Prefer to bring the target itself to 24×24 CSS px "
                "(padding is fine; box-size doesn't have to grow). "
                "If the spacing exception must remain, document the "
                "minimum surrounding white-space in the design system."
            )
        else:
            description = (
                "This pointer target is smaller than the 24×24 CSS-pixel "
                "minimum and another pointer target sits within a "
                "24-pixel-diameter circle centered on it. Users with "
                "motor impairments and touch-screen users cannot reliably "
                "activate it without mis-tapping a neighbour. WCAG 2.5.8 "
                "(AA, new in 2.2) requires either an adequate target "
                "size, sufficient spacing, an inline-text context, or an "
                "essential reason — none currently apply."
            )
            fix = (
                "Increase the target to 24×24 CSS px (padding works), "
                "or space adjacent targets so a 24-pixel circle around "
                "each contains no other target. Inline text-flow targets "
                "and equivalent-control alternatives are exempt — if one "
                "of those applies here, document it; this rule cannot "
                "detect the equivalent-control exception automatically."
            )
        issues.append(
            make_issue(
                issue_id=f"target-size-{idx}",
                module="target_size",
                rule=rule_id,
                severity=severity,
                wcag=["2.5.8"],
                confidence="high" if not spacing_ok else "medium",
                title=title,
                description=description,
                selector=f.get("selector", ""),
                html_snippet=f.get("html_snippet", ""),
                details={
                    "width_px": f.get("width"),
                    "height_px": f.get("height"),
                    "minimum_px": MIN_PX,
                    "spacing_exception_applies": spacing_ok,
                    "accessible_name": f.get("accessible_name", ""),
                },
                fix=fix,
            )
        )

    return {
        "ran": True,
        "issues": issues,
        "candidate_count": len(findings),
        "target_count": target_count,
        "min_gap_px": min_gap_px,
        "min_gap_pair": min_gap_pair,
        "reported": len(issues),
        "truncated": len(findings) > MAX_REPORTED,
        "duration_seconds": round(time.time() - start, 3),
    }
