"""Cognitive module: link clarity and related content-comprehension checks.

Rules implemented:
- cognitive-generic-link-text     WCAG 2.4.4  moderate  link text is a generic phrase like "click here"
- cognitive-duplicate-link-text   WCAG 2.4.4  moderate  multiple links share text but point to different URLs
- cognitive-empty-link            WCAG 2.4.4  serious   link has no accessible text at all

Note: reading-level analysis is planned but not wired in this cut — it needs
the `textstat` dependency which we don't pull in yet.
"""

from __future__ import annotations

import re
import time
from typing import Any

from audit._issue import make_issue

GENERIC_PHRASES = {
    "click here",
    "click",
    "here",
    "read more",
    "more",
    "learn more",
    "details",
    "info",
    "link",
    "this",
    "this link",
    "this page",
}

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


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
    function accessibleName(a) {
        const aria = a.getAttribute('aria-label');
        if (aria) return aria.trim();
        const labelledby = a.getAttribute('aria-labelledby');
        if (labelledby) {
            const parts = labelledby.split(/\s+/).map(id => {
                const ref = document.getElementById(id);
                return ref ? (ref.textContent || '').trim() : '';
            });
            return parts.filter(Boolean).join(' ');
        }
        const img = a.querySelector('img[alt]');
        const txt = (a.textContent || '').trim();
        if (txt) return txt;
        if (img) return (img.getAttribute('alt') || '').trim();
        return '';
    }
    return [...document.querySelectorAll('a[href]')].map(a => ({
        text: accessibleName(a),
        href: a.getAttribute('href') || '',
        selector: cssPath(a),
        html: a.outerHTML.slice(0, 200)
    }));
}
"""


def analyze(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    # generic / empty
    by_text_href: dict[str, set[str]] = {}
    by_text_links: dict[str, list[dict[str, Any]]] = {}

    for idx, link in enumerate(links):
        text = link.get("text", "")
        norm = _normalize(text)
        href = link.get("href", "")

        if not norm:
            issues.append(
                make_issue(
                    issue_id=f"cognitive-empty-link-{idx}",
                    module="cognitive",
                    rule="cognitive-empty-link",
                    severity="serious",
                    principle="understandable",
                    wcag=["2.4.4"],
                    title="Link has no accessible text",
                    description=(
                        "A link with empty text (and no aria-label or image alt) is "
                        "announced as 'link' with no indication of its purpose."
                    ),
                    selector=link.get("selector", ""),
                    html_snippet=link.get("html", ""),
                    details={"href": href},
                    fix="Add visible text, aria-label, or an image with descriptive alt.",
                )
            )
            continue

        if norm in GENERIC_PHRASES:
            issues.append(
                make_issue(
                    issue_id=f"cognitive-generic-link-text-{idx}",
                    module="cognitive",
                    rule="cognitive-generic-link-text",
                    severity="moderate",
                    principle="understandable",
                    wcag=["2.4.4"],
                    title=f'Link text "{text.strip()}" is not descriptive',
                    description=(
                        "Screen-reader users often navigate by scanning a list of links. "
                        "Generic phrases like 'click here' lose all context in that view."
                    ),
                    selector=link.get("selector", ""),
                    html_snippet=link.get("html", ""),
                    text=text,
                    details={"href": href},
                    fix="Rewrite the link text to describe the destination or action.",
                )
            )

        by_text_href.setdefault(norm, set()).add(href)
        by_text_links.setdefault(norm, []).append(link)

    # duplicate text → different URLs
    for norm, hrefs in by_text_href.items():
        if len(hrefs) > 1 and norm not in GENERIC_PHRASES:
            dup_links = by_text_links[norm]
            # Report only the second-plus occurrences to avoid spamming.
            for dup_idx, link in enumerate(dup_links[1:], start=1):
                issues.append(
                    make_issue(
                        issue_id=f"cognitive-duplicate-link-text-{norm}-{dup_idx}",
                        module="cognitive",
                        rule="cognitive-duplicate-link-text",
                        severity="moderate",
                        principle="understandable",
                        wcag=["2.4.4"],
                        title=f'Multiple links with text "{link.get("text", "").strip()}" go to different URLs',
                        description=(
                            "Users relying on link lists cannot distinguish between "
                            "links that share identical text but different destinations."
                        ),
                        selector=link.get("selector", ""),
                        html_snippet=link.get("html", ""),
                        text=link.get("text", ""),
                        details={
                            "text": link.get("text", ""),
                            "distinct_urls": sorted(hrefs),
                        },
                        fix="Differentiate the text (e.g. 'Read the 2025 report' vs 'Read the 2024 report').",
                    )
                )

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        links = page.evaluate(_EXTRACT_JS)
    except Exception as exc:
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(links)
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "links_analyzed": len(links),
    }
