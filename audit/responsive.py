"""Responsive module: viewport meta + target size (WCAG 2.5.8).

Scoped to rules that work off static DOM / layout geometry:
- responsive-viewport-meta-missing    WCAG 1.4.4, 1.4.10  serious   no <meta name="viewport">
- responsive-viewport-zoom-disabled   WCAG 1.4.4          serious   viewport meta disables user zoom
- responsive-target-size              WCAG 2.5.8 (2.2 AA) moderate  interactive target < 24x24 CSS pixels

The plan's reflow-at-320px and text-spacing-override rules are intentionally
deferred: they need real page manipulation (resize, CSS injection, overflow
detection) and are prone to false positives when run as a static snapshot.
They'll land behind a separate interactive-checks pass.
"""

from __future__ import annotations

import re
import time
from typing import Any

from audit._issue import make_issue

MIN_TARGET_SIZE = 24  # CSS pixels, WCAG 2.5.8 AA minimum.

# WCAG 2.5.8 exceptions we model here: inline targets (inside flowing text),
# disabled controls, and off-screen / 0-size elements.
EXEMPT_DISPLAY = {"inline"}


_EXTRACT_JS = r"""
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

    const meta = document.querySelector('meta[name="viewport"]');
    const viewport = meta
        ? { present: true, content: meta.getAttribute('content') || '' }
        : { present: false, content: '' };

    const selector = [
        'a[href]',
        'button',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        '[role="button"]',
        '[role="link"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="switch"]',
        '[role="tab"]',
        '[role="menuitem"]'
    ].join(',');

    const targets = [...document.querySelectorAll(selector)]
        .filter(el => !el.hidden && !el.disabled)
        .map(el => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return {
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                role: el.getAttribute('role') || '',
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                display: style.display,
                visibility: style.visibility,
                offscreen: rect.width === 0 || rect.height === 0,
                selector: cssPath(el),
                html: el.outerHTML.slice(0, 200)
            };
        });

    return { viewport, targets };
}
"""


_MAX_SCALE_RE = re.compile(r"maximum-scale\s*=\s*([0-9.]+)", re.IGNORECASE)
_USER_SCALABLE_RE = re.compile(r"user-scalable\s*=\s*(no|0)", re.IGNORECASE)


def _zoom_disabled(content: str) -> bool:
    if not content:
        return False
    if _USER_SCALABLE_RE.search(content):
        return True
    m = _MAX_SCALE_RE.search(content)
    if m:
        try:
            return float(m.group(1)) < 2.0
        except ValueError:
            return False
    return False


def analyze(dom: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    viewport = dom.get("viewport") or {}
    if not viewport.get("present"):
        issues.append(
            make_issue(
                issue_id="responsive-viewport-meta-missing",
                module="responsive",
                rule="responsive-viewport-meta-missing",
                severity="serious",
                principle="perceivable",
                wcag=["1.4.4", "1.4.10"],
                title="Page has no <meta name=\"viewport\"> tag",
                description=(
                    "Without a viewport meta the page is rendered at desktop width on "
                    "mobile and then scaled down, defeating zoom and reflow."
                ),
                selector="head",
                fix='Add <meta name="viewport" content="width=device-width, initial-scale=1"> inside <head>.',
            )
        )
    elif _zoom_disabled(viewport.get("content", "")):
        issues.append(
            make_issue(
                issue_id="responsive-viewport-zoom-disabled",
                module="responsive",
                rule="responsive-viewport-zoom-disabled",
                severity="serious",
                principle="perceivable",
                wcag=["1.4.4"],
                title="Viewport meta disables user zoom",
                description=(
                    "Using user-scalable=no or maximum-scale below 2.0 prevents users "
                    "with low vision from zooming text to a readable size."
                ),
                selector='meta[name="viewport"]',
                details={"content": viewport.get("content", "")},
                fix="Remove user-scalable=no and raise maximum-scale to at least 2.0 (or omit it).",
            )
        )

    for idx, t in enumerate(dom.get("targets") or []):
        if t.get("offscreen") or t.get("visibility") == "hidden":
            continue
        if (t.get("display") or "") in EXEMPT_DISPLAY:
            # Inline targets embedded in flowing text are exempt per 2.5.8.
            continue
        w = int(t.get("width", 0))
        h = int(t.get("height", 0))
        if w >= MIN_TARGET_SIZE and h >= MIN_TARGET_SIZE:
            continue
        issues.append(
            make_issue(
                issue_id=f"responsive-target-size-{idx}",
                module="responsive",
                rule="responsive-target-size",
                severity="moderate",
                principle="operable",
                wcag=["2.5.8"],
                title=f"Interactive target is only {w}x{h}px (minimum {MIN_TARGET_SIZE}x{MIN_TARGET_SIZE})",
                description=(
                    "WCAG 2.2 AA requires interactive targets to be at least "
                    f"{MIN_TARGET_SIZE}x{MIN_TARGET_SIZE} CSS pixels so users with motor "
                    "impairments can activate them reliably."
                ),
                selector=t.get("selector", ""),
                html_snippet=t.get("html", ""),
                details={
                    "width": w,
                    "height": h,
                    "tag": t.get("tag"),
                    "role": t.get("role"),
                    "type": t.get("type"),
                },
                fix=f"Increase the target's padding or minimum size to at least {MIN_TARGET_SIZE}x{MIN_TARGET_SIZE}px.",
            )
        )

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        dom = page.evaluate(_EXTRACT_JS)
    except Exception as exc:
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(dom)
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "targets_measured": len(dom.get("targets") or []),
        "viewport": dom.get("viewport") or {},
    }
