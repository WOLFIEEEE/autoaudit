"""Structure module: lang, title, headings, landmarks, tables.

Rules implemented:
- structure-html-lang      WCAG 3.1.1  serious   <html> missing lang
- structure-title-missing  WCAG 2.4.2  serious   <title> missing or empty
- structure-no-h1          WCAG 1.3.1  moderate  no <h1> found
- structure-multiple-h1    WCAG 1.3.1  minor     more than one <h1>
- structure-heading-skip   WCAG 1.3.1  moderate  heading level skipped (e.g. h1 -> h3)
- structure-no-main        WCAG 1.3.1  moderate  no <main> or role=main landmark
- structure-table-no-th    WCAG 1.3.1  serious   <table> without any <th>

Overlaps with axe-core in some rules; the cross-module deduplicator merges
them so users see one issue per element per rule family.
"""

from __future__ import annotations

import time
from typing import Any

from audit._issue import make_issue

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
    const html = document.documentElement;
    const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => ({
        level: Number(h.tagName[1]),
        text: (h.textContent || '').trim().slice(0, 160),
        selector: cssPath(h),
        html: h.outerHTML.slice(0, 200)
    }));
    const landmarks = {
        main: document.querySelectorAll('main, [role="main"]').length,
        nav: document.querySelectorAll('nav, [role="navigation"]').length,
        banner: document.querySelectorAll('header, [role="banner"]').length,
        contentinfo: document.querySelectorAll('footer, [role="contentinfo"]').length,
    };
    const tables = [...document.querySelectorAll('table')].map(t => ({
        has_th: t.querySelector('th') !== null,
        has_caption: t.querySelector('caption') !== null,
        selector: cssPath(t),
        html: t.outerHTML.slice(0, 200)
    }));
    return {
        lang: html.getAttribute('lang'),
        title: document.title,
        headings,
        landmarks,
        tables
    };
}
"""


def analyze(dom: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    # lang
    lang = (dom.get("lang") or "").strip()
    if not lang:
        issues.append(
            make_issue(
                issue_id="structure-html-lang",
                module="structure",
                rule="structure-html-lang",
                severity="serious",
                wcag=["3.1.1"],
                title="<html> element is missing a lang attribute",
                description=(
                    "The document's primary language is not declared. Screen readers use "
                    "lang to choose pronunciation rules."
                ),
                selector="html",
                fix='Add a lang attribute, e.g. <html lang="en">.',
            )
        )

    # title
    title = (dom.get("title") or "").strip()
    if not title:
        issues.append(
            make_issue(
                issue_id="structure-title-missing",
                module="structure",
                rule="structure-title-missing",
                severity="serious",
                wcag=["2.4.2"],
                title="<title> is missing or empty",
                description="Every page needs a unique, descriptive <title> element.",
                selector="title",
                fix="Add a descriptive <title> inside <head>.",
            )
        )

    # headings
    headings = dom.get("headings") or []
    h1s = [h for h in headings if h.get("level") == 1]
    if not h1s:
        issues.append(
            make_issue(
                issue_id="structure-no-h1",
                module="structure",
                rule="structure-no-h1",
                severity="moderate",
                wcag=["1.3.1"],
                title="Page has no <h1>",
                description="A top-level heading helps users orient themselves.",
                fix="Add one <h1> describing the page's primary content.",
            )
        )
    elif len(h1s) > 1:
        issues.append(
            make_issue(
                issue_id="structure-multiple-h1",
                module="structure",
                rule="structure-multiple-h1",
                severity="minor",
                wcag=["1.3.1"],
                title=f"Page has {len(h1s)} <h1> elements",
                description=(
                    "Multiple <h1> elements can confuse screen-reader navigation. "
                    "Prefer one <h1> per page with <h2> for sections."
                ),
                selector=h1s[1].get("selector", ""),
                html_snippet=h1s[1].get("html", ""),
                details={"count": len(h1s)},
                fix="Keep a single <h1>; demote the extras to <h2>.",
            )
        )

    # heading level skips
    prev_level = 0
    for h in headings:
        level = h.get("level", 0)
        if prev_level and level > prev_level + 1:
            issues.append(
                make_issue(
                    issue_id=f"structure-heading-skip-{h.get('selector','')}",
                    module="structure",
                    rule="structure-heading-skip",
                    severity="moderate",
                    wcag=["1.3.1"],
                    title=f"Heading jumps from h{prev_level} to h{level}",
                    description=(
                        "Skipping heading levels breaks the document outline that "
                        "assistive tech relies on."
                    ),
                    selector=h.get("selector", ""),
                    html_snippet=h.get("html", ""),
                    text=h.get("text", ""),
                    details={"from_level": prev_level, "to_level": level},
                    fix=f"Use h{prev_level + 1} instead, or add an intermediate heading.",
                )
            )
        prev_level = level

    # landmarks
    landmarks = dom.get("landmarks") or {}
    if not landmarks.get("main"):
        issues.append(
            make_issue(
                issue_id="structure-no-main",
                module="structure",
                rule="structure-no-main",
                severity="moderate",
                wcag=["1.3.1"],
                title="No <main> landmark",
                description=(
                    "A <main> landmark lets screen-reader users skip directly to the "
                    "primary content."
                ),
                fix='Wrap the primary content in <main> or add role="main".',
            )
        )

    # tables
    for idx, t in enumerate(dom.get("tables") or []):
        if not t.get("has_th"):
            issues.append(
                make_issue(
                    issue_id=f"structure-table-no-th-{idx}",
                    module="structure",
                    rule="structure-table-no-th",
                    severity="serious",
                    wcag=["1.3.1"],
                    title="Data table has no <th> headers",
                    description=(
                        "Tables without <th> cells are announced as a flat grid, losing "
                        "the row/column context screen readers need."
                    ),
                    selector=t.get("selector", ""),
                    html_snippet=t.get("html", ""),
                    fix="Mark header cells with <th> and use scope='col' or scope='row'.",
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
        "headings": len(dom.get("headings") or []),
        "landmarks": dom.get("landmarks") or {},
        "tables": len(dom.get("tables") or []),
    }
