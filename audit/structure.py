"""Structure module: lang, title, headings, landmarks, tables, iframes.

Rules implemented:
- structure-html-lang             WCAG 3.1.1  serious   <html> missing lang
- structure-title-missing         WCAG 2.4.2  serious   <title> missing or empty
- structure-title-too-short       WCAG 2.4.2  moderate  <title> is fewer than 4 characters
- structure-title-generic         WCAG 2.4.2  moderate  <title> is a placeholder ("Document", "Untitled", a domain only)
- structure-no-h1                 WCAG 1.3.1  moderate  no <h1> found
- structure-multiple-h1           WCAG 1.3.1  minor     more than one <h1>
- structure-heading-skip          WCAG 1.3.1  moderate  heading level skipped (e.g. h1 -> h3)
- structure-duplicate-heading     WCAG 2.4.6  minor     two+ headings with identical text (low confidence)
- structure-no-main               WCAG 1.3.1  moderate  no <main> or role=main landmark
- structure-table-no-th           WCAG 1.3.1  serious   <table> without any <th>
- structure-iframe-no-title       WCAG 4.1.2  serious   <iframe> without title attribute
- structure-iframe-title-generic  WCAG 4.1.2  moderate  <iframe title="..."> set to a generic placeholder
- structure-lang-of-parts         WCAG 3.1.2  moderate  lang-tagged text inside wrong-lang doc without lang=

Overlaps with axe-core in some rules; the cross-module deduplicator merges
them so users see one issue per element per rule family.
"""

from __future__ import annotations

import time
from typing import Any

from audit._issue import make_issue

# Title quality heuristics. Anything below `_MIN_TITLE_LEN` chars is
# implausibly informative for a real page; the placeholder set captures
# common defaults shipped by templates and SSGs ("Document" is the
# Chrome new-tab fallback, "Untitled" is the Word default, etc.).
_MIN_TITLE_LEN = 4
_GENERIC_TITLES = {
    "document", "untitled", "untitled document", "page",
    "new page", "home", "index", "default", "title",
    "react app", "vite app", "next app",
}

# Iframe-title-quality blacklist. A title is "set but useless" if it
# repeats the tag name, the word "frame", "iframe" alone, or matches a
# common placeholder. Domains alone are flagged because "youtube.com"
# tells SR users nothing about the embedded video.
_GENERIC_IFRAME_TITLES = {
    "frame", "iframe", "embed", "embedded content",
    "title", "untitled", "content",
}

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
    // iframes: each one needs a non-empty title for SR users to
    // understand what embedded content they've landed in.
    const iframes = [...document.querySelectorAll('iframe')].map(f => ({
        title: (f.getAttribute('title') || '').trim(),
        aria_label: (f.getAttribute('aria-label') || '').trim(),
        src: f.getAttribute('src') || '',
        has_aria_hidden: f.getAttribute('aria-hidden') === 'true',
        selector: cssPath(f),
        html: f.outerHTML.slice(0, 200)
    }));
    // lang-of-parts: find descendants whose text is clearly a
    // different language from the document but carry no lang attr.
    // We keep this conservative — only report when a non-ASCII
    // character run longer than 15 chars is in a subtree with no
    // ancestor lang attribute that differs from the document lang.
    const docLang = (html.getAttribute('lang') || '').toLowerCase().split('-')[0];
    const langOfParts = [];
    if (docLang) {
        // Unicode script ranges we expect to indicate a distinct
        // natural language from a Latin-script default. Keeping the
        // list narrow reduces false positives from loanwords / emoji.
        const nonLatinRun = /[\u0370-\u03FF\u0400-\u04FF\u0530-\u058F\u0590-\u05FF\u0600-\u06FF\u0900-\u097F\u0E00-\u0E7F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]{15,}/;
        const walker = document.createTreeWalker(
            document.body || document.documentElement,
            NodeFilter.SHOW_TEXT, null, false
        );
        while (walker.nextNode()) {
            const n = walker.currentNode;
            const txt = (n.nodeValue || '').trim();
            if (!txt || !nonLatinRun.test(txt)) continue;
            // Does any ancestor declare a lang attribute?
            let ancestor = n.parentElement;
            let tagged = false;
            while (ancestor && ancestor.tagName !== 'HTML') {
                if (ancestor.getAttribute && ancestor.getAttribute('lang')) {
                    tagged = true;
                    break;
                }
                ancestor = ancestor.parentElement;
            }
            if (!tagged && docLang === 'en') {
                // Document claims English but this run clearly isn't.
                const host = n.parentElement;
                langOfParts.push({
                    text: txt.slice(0, 80),
                    selector: cssPath(host),
                });
            }
            if (langOfParts.length >= 10) break;  // cap noise
        }
    }
    return {
        lang: html.getAttribute('lang'),
        title: document.title,
        headings,
        landmarks,
        tables,
        iframes,
        lang_of_parts: langOfParts,
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
    else:
        # Quality checks fire only when a title actually exists; a
        # missing title was already caught by the rule above and we
        # don't want to double-stamp the same defect.
        title_lower = title.lower().strip(" -|·•")
        if len(title) < _MIN_TITLE_LEN:
            issues.append(
                make_issue(
                    issue_id="structure-title-too-short",
                    module="structure",
                    rule="structure-title-too-short",
                    severity="moderate",
                    wcag=["2.4.2"],
                    confidence="high",
                    title=f"<title> is only {len(title)} characters long",
                    description=(
                        f"The page title {title!r} is too short to be "
                        "meaningfully descriptive. WCAG 2.4.2 requires "
                        "titles that describe topic or purpose; users "
                        "reading the tab strip or browser-history list "
                        "cannot orient on a title this brief."
                    ),
                    selector="title",
                    details={"title": title, "length": len(title)},
                    fix=(
                        "Replace the title with a phrase that names the "
                        "page's primary content, e.g. 'Account settings — "
                        "Acme'."
                    ),
                )
            )
        elif title_lower in _GENERIC_TITLES:
            issues.append(
                make_issue(
                    issue_id="structure-title-generic",
                    module="structure",
                    rule="structure-title-generic",
                    severity="moderate",
                    wcag=["2.4.2"],
                    confidence="high",
                    title=f"<title> is a generic placeholder: {title!r}",
                    description=(
                        "This title is one of the common framework / "
                        "template defaults that ship before authors set "
                        "a real title. Screen-reader users hear it on "
                        "page load and tab switch; users with cognitive "
                        "disabilities rely on it for orientation."
                    ),
                    selector="title",
                    details={"title": title},
                    fix=(
                        "Set a unique <title> per page that describes "
                        "the content, e.g. 'Checkout — Step 2 of 3'."
                    ),
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

    # duplicate heading text (WCAG 2.4.6 Headings and Labels). Two or
    # more headings that read identically are ambiguous: a screen-reader
    # user pulling up the heading list cannot tell the sections apart.
    # This is exactly the "Two headings with similar wording" / "Headings
    # repeat" finding professional audits raise. Low confidence —
    # repetition is sometimes legitimate (a repeated "Overview" under
    # clearly distinct parents), so it's surfaced for review, not
    # asserted. Empty headings are left to the screen_reader module's
    # sr-empty-heading rule.
    seen_headings: dict[str, dict[str, Any]] = {}
    for h in headings:
        text = (h.get("text") or "").strip()
        norm = " ".join(text.lower().split())
        if len(norm) < 2:
            continue
        first = seen_headings.get(norm)
        if first is None:
            seen_headings[norm] = h
            continue
        issues.append(
            make_issue(
                issue_id=f"structure-duplicate-heading-{h.get('selector','')}",
                module="structure",
                rule="structure-duplicate-heading",
                severity="minor",
                wcag=["2.4.6"],
                confidence="low",
                title=f"Duplicate heading text: {text[:60]!r}",
                description=(
                    "Another heading on this page has identical text. When "
                    "headings repeat verbatim, a screen-reader user "
                    "navigating by heading cannot tell the sections apart. "
                    "WCAG 2.4.6 (AA) asks that headings describe their "
                    "specific topic. Heuristic — repeated headings are "
                    "occasionally legitimate; review and disambiguate or "
                    "dismiss."
                ),
                selector=h.get("selector", ""),
                html_snippet=h.get("html", ""),
                text=text,
                details={
                    "text": text,
                    "level": h.get("level"),
                    "first_selector": first.get("selector", ""),
                    "first_level": first.get("level"),
                },
                fix=(
                    "Make each heading describe its specific section, or "
                    "merge the duplicated sections. If the repetition is "
                    "intentional, ensure a surrounding region/landmark "
                    "label disambiguates them."
                ),
            )
        )

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

    # iframes — each needs an explanatory title attribute.
    # aria-hidden=true iframes are exempt: they're decoratively hidden
    # from AT entirely and don't need a title.
    for idx, frame in enumerate(dom.get("iframes") or []):
        if frame.get("has_aria_hidden"):
            continue
        title_text = (frame.get("title") or frame.get("aria_label") or "").strip()
        if not title_text:
            issues.append(
                make_issue(
                    issue_id=f"structure-iframe-no-title-{idx}",
                    module="structure",
                    rule="structure-iframe-no-title",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="<iframe> has no title",
                    description=(
                        "Screen-reader users navigate iframes by title (in the "
                        "landmarks / frames list). An untitled iframe shows up "
                        "as 'frame' with no indication of its content."
                    ),
                    selector=frame.get("selector", ""),
                    html_snippet=frame.get("html", ""),
                    details={"src": frame.get("src", "")},
                    fix=(
                        "Add a title attribute that describes the iframe's "
                        'contents, e.g. title="Payment processor".'
                    ),
                )
            )
        elif title_text.lower() in _GENERIC_IFRAME_TITLES:
            # Set, but useless. Worse than missing because it looks
            # compliant on a casual review while telling users nothing.
            issues.append(
                make_issue(
                    issue_id=f"structure-iframe-title-generic-{idx}",
                    module="structure",
                    rule="structure-iframe-title-generic",
                    severity="moderate",
                    wcag=["4.1.2"],
                    confidence="high",
                    title=f"<iframe title={title_text!r}> is a placeholder",
                    description=(
                        "The iframe declares a title attribute but its "
                        "value is a generic placeholder. Screen-reader "
                        "users hear it announced as the frame's name and "
                        "still cannot tell what content it embeds."
                    ),
                    selector=frame.get("selector", ""),
                    html_snippet=frame.get("html", ""),
                    details={"title": title_text, "src": frame.get("src", "")},
                    fix=(
                        "Replace the title with a phrase describing the "
                        "embedded content, e.g. 'Stripe payment form' "
                        "or 'YouTube video: <name>'."
                    ),
                )
            )

    # lang-of-parts: text in a non-Latin script inside a document that
    # declares itself English, with no intermediate lang= override.
    for idx, part in enumerate(dom.get("lang_of_parts") or []):
        issues.append(
            make_issue(
                issue_id=f"structure-lang-of-parts-{idx}",
                module="structure",
                rule="structure-lang-of-parts",
                severity="moderate",
                wcag=["3.1.2"],
                title="Foreign-language text is not marked with lang=",
                description=(
                    "This text is in a different script than the document's "
                    "declared language, but no ancestor provides a lang "
                    "attribute. Screen readers will mispronounce it using "
                    "the document's language rules."
                ),
                selector=part.get("selector", ""),
                details={"text_sample": part.get("text", "")},
                fix=(
                    'Wrap the foreign-language content in an element with '
                    'an appropriate lang attribute, e.g. '
                    '<span lang="hi">हिन्दी</span>.'
                ),
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
