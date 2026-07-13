"""Cognitive module: link clarity and content-comprehension checks.

Rules implemented:
- cognitive-generic-link-text     WCAG 2.4.4  moderate  link text is a generic phrase like "click here"
- cognitive-duplicate-link-text   WCAG 2.4.4  moderate  multiple links share text but point to different URLs
- cognitive-empty-link            WCAG 2.4.4  serious   link has no accessible text at all
- cognitive-reading-level-high    WCAG 3.1.5  minor     main content reads above lower-secondary grade

Reading-level uses Flesch-Kincaid Grade Level computed inline (no
external textstat dependency). WCAG 3.1.5 is AAA, so this fires as a
low-severity advisory rather than a conformance blocker.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

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

# Phrases that are non-descriptive no matter what follows them — "click
# here to view", "click here for details", etc. Matched as a prefix so
# trailing filler doesn't rescue them. Kept narrow ("click here"/"click
# to") to avoid catching genuinely-descriptive links that merely start
# with "read more about the nursing program".
_GENERIC_PREFIXES = ("click here", "click to")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _is_generic_link_text(norm: str) -> bool:
    """True when link text is a generic, context-free phrase (2.4.4)."""
    if norm in GENERIC_PHRASES:
        return True
    return any(norm.startswith(p) for p in _GENERIC_PREFIXES)


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

        if _is_generic_link_text(norm):
            issues.append(
                make_issue(
                    issue_id=f"cognitive-generic-link-text-{idx}",
                    module="cognitive",
                    rule="cognitive-generic-link-text",
                    severity="moderate",
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


# ---------------------------------------------------------------------
# Reading level (WCAG 3.1.5 — AAA, "lower secondary education level").
# We compute Flesch-Kincaid grade inline instead of pulling in textstat.
# Target: grade <= 8 (US 8th grade ~= lower secondary). We flag > 10 to
# avoid false positives on content that's only marginally above target.

_READING_EXTRACT_JS = r"""() => {
    function collectText(root) {
        const clone = root.cloneNode(true);
        for (const sel of ['nav', 'footer', 'header[role="banner"]', 'aside',
                           'script', 'style', 'noscript', '[role="navigation"]',
                           '[role="complementary"]', '[aria-hidden="true"]']) {
            for (const n of clone.querySelectorAll(sel)) n.remove();
        }
        return (clone.textContent || '').replace(/\s+/g, ' ').trim();
    }
    const main = document.querySelector('main, [role="main"], article') || document.body;
    if (!main) return '';
    return collectText(main).slice(0, 5000);
}"""

_SYLLABLE_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)
_WORD_RE = re.compile(r"\b[a-zA-Z]+\b")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+|\n")

READING_GRADE_TARGET = 8.0
READING_GRADE_FLAG_THRESHOLD = 10.0
MIN_WORDS_FOR_READING_ANALYSIS = 50


def _count_syllables(word: str) -> int:
    """Rough English syllable count. Good enough for FK — not perfect.

    We don't need linguistic accuracy; FK is a coarse grade estimate
    regardless of syllable algorithm. This gets us within ~0.5 grade of
    textstat and avoids the dependency.
    """
    word = re.sub(r"e\b", "", word.lower())
    if not word:
        return 0
    return max(1, len(_SYLLABLE_GROUP_RE.findall(word)))


def _flesch_kincaid_grade(text: str) -> float | None:
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    words = _WORD_RE.findall(text)
    if len(words) < MIN_WORDS_FOR_READING_ANALYSIS or not sentences:
        return None
    syllables = sum(_count_syllables(w) for w in words)
    words_per_sentence = len(words) / len(sentences)
    syllables_per_word = syllables / len(words)
    return round(0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59, 1)


def analyze_reading_level(text: str) -> list[dict[str, Any]]:
    grade = _flesch_kincaid_grade(text)
    if grade is None or grade <= READING_GRADE_FLAG_THRESHOLD:
        return []
    return [make_issue(
        issue_id="cognitive-reading-level-high",
        module="cognitive",
        rule="cognitive-reading-level-high",
        severity="minor",
        wcag=["3.1.5"],
        title=f"Main content reads at approximately grade {grade}",
        description=(
            "WCAG 3.1.5 (AAA) asks that content be written at or below lower "
            "secondary education level (US grade ~8). The Flesch-Kincaid "
            "estimate for the visible main content on this page is higher than "
            "that target, which can exclude readers with cognitive disabilities "
            "or lower literacy."
        ),
        confidence="medium",
        details={
            "flesch_kincaid_grade": grade,
            "target_grade": READING_GRADE_TARGET,
            "threshold_grade": READING_GRADE_FLAG_THRESHOLD,
        },
        fix=(
            "Shorten sentences, swap jargon for plain alternatives, prefer "
            "active voice, break long paragraphs, and consider a plain-language "
            "summary for key sections."
        ),
    )]


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

    # Reading-level is a best-effort add-on — a page-level error here
    # shouldn't blank out the link analysis that already succeeded.
    reading_text = ""
    try:
        reading_text = page.evaluate(_READING_EXTRACT_JS) or ""
    except Exception as exc:
        log.debug("reading-level extraction failed: %s", exc)
    reading_issues = analyze_reading_level(reading_text) if reading_text else []
    issues.extend(reading_issues)

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "links_analyzed": len(links),
        "reading_level_analyzed": bool(reading_text),
    }
