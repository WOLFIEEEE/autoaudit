"""Document language detection — extends WCAG 3.1.1 (A) checks.

`structure-html-lang` catches **missing** `<html lang>`. This module
catches the more insidious case: `<html lang="en">` set on a page
whose actual content is in another language. Common when a CMS
template ships with a default `lang="en"` and the localised content
is rendered into the body.

Approach: cheap unicode-script-frequency heuristic. We sample the
first ~2000 characters of body text, classify each character's
Unicode script (Latin / Cyrillic / Greek / Arabic / Devanagari /
CJK / etc.), and compare the dominant script to what the declared
`lang` would predict.

This is **not** language identification (cld3/franc would be more
accurate for "is this German or Dutch?"). It is *script* detection
— enough to catch lang="en" on a Hindi page, but it won't
distinguish lang="en" on a Spanish page.

That limitation is honest: pure-script heuristics produce strong
findings ("declared English, actually Cyrillic") with very few
false positives. Real natural-language detection is moonshot
territory and adds a heavy dependency.

Rules emitted:

- `structure-lang-content-mismatch`  WCAG 3.1.1  serious  declared lang's primary script
                                                          disagrees with content
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Map ISO 639-1 / 639-3 language codes to the Unicode script we
# expect to see in their body text. Only well-known codes — no need
# to be exhaustive, the rule fires only on strong mismatches.
_LANG_TO_PRIMARY_SCRIPT: dict[str, str] = {
    # Latin-script
    "en": "Latin", "es": "Latin", "fr": "Latin", "de": "Latin",
    "it": "Latin", "pt": "Latin", "nl": "Latin", "sv": "Latin",
    "da": "Latin", "no": "Latin", "fi": "Latin", "is": "Latin",
    "pl": "Latin", "cs": "Latin", "sk": "Latin", "hu": "Latin",
    "ro": "Latin", "tr": "Latin", "vi": "Latin", "id": "Latin",
    "ms": "Latin", "tl": "Latin", "sw": "Latin", "af": "Latin",
    # Cyrillic
    "ru": "Cyrillic", "uk": "Cyrillic", "bg": "Cyrillic",
    "be": "Cyrillic", "sr": "Cyrillic", "mk": "Cyrillic",
    "kk": "Cyrillic", "ky": "Cyrillic",
    # Greek
    "el": "Greek",
    # Arabic-script
    "ar": "Arabic", "fa": "Arabic", "ur": "Arabic", "ps": "Arabic",
    # Hebrew
    "he": "Hebrew", "iw": "Hebrew", "yi": "Hebrew",
    # Indic
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari",
    "bn": "Bengali",
    "pa": "Gurmukhi",
    "gu": "Gujarati",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "si": "Sinhala",
    # SE Asian
    "th": "Thai",
    "lo": "Lao",
    "km": "Khmer",
    "my": "Myanmar",
    # CJK
    "zh": "Han",   # Chinese — Simplified or Traditional, both Han
    "ja": "Han",   # Japanese — mixed Han + Hiragana + Katakana; we
                   # accept Han as primary for the heuristic
    "ko": "Hangul",
    # Other
    "ka": "Georgian",
    "hy": "Armenian",
    "am": "Ethiopic",
}

# Minimum sample size before we trust the script-frequency signal.
# Tiny pages (e.g. landing pages with image-only marketing) might
# legitimately have <40 chars of text.
_MIN_SAMPLE_CHARS = 40
# Threshold for "the body is dominated by script X". Chosen high
# enough that mixed-script pages (English with sprinkles of Greek
# math) don't trip the rule.
_DOMINANCE_THRESHOLD = 0.65

_PROBE_JS = r"""
() => {
    const body = document.body;
    if (!body) return {lang: '', text: ''};
    return {
        lang: (document.documentElement.getAttribute('lang') || '').trim().toLowerCase(),
        // First ~3000 chars of innerText. innerText respects
        // visibility (won't include display:none chunks), which is
        // exactly the right signal — we want the content the user
        // actually sees.
        text: (body.innerText || '').slice(0, 3000),
    };
}
"""


def _script_of(ch: str) -> str:
    """Return the Unicode script name for one character ('Latin',
    'Cyrillic', 'Han', etc.). Falls back to "Common" for digits,
    punctuation, etc., which we discard before measuring."""
    if not ch:
        return "Common"
    # `unicodedata.name` returns names like "LATIN SMALL LETTER A",
    # "CYRILLIC CAPITAL LETTER A", "DEVANAGARI LETTER KA". The first
    # token before " " is the script in 99% of cases. Outliers
    # ("ARABIC-INDIC DIGIT") fall back to "Common" via the digit
    # filter below.
    if not ch.isalpha():
        return "Common"
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "Common"
    first = name.split(" ")[0]
    # Normalise a few aliases.
    if first in ("HIRAGANA", "KATAKANA"):
        return "Han"  # Folded into the same bucket as Japanese kanji
    if first == "CJK":
        return "Han"
    return first.title()


def _dominant_script(text: str) -> tuple[str, float, int]:
    """Return (dominant_script, share, sampled_count).

    `share` is the fraction of *alphabetic* characters belonging to
    the dominant script — punctuation and digits are excluded so
    multilingual UIs with shared numbers don't skew the result.
    """
    counts: dict[str, int] = {}
    sampled = 0
    for ch in text:
        s = _script_of(ch)
        if s == "Common":
            continue
        counts[s] = counts.get(s, 0) + 1
        sampled += 1
    if not counts or sampled < _MIN_SAMPLE_CHARS:
        return ("", 0.0, sampled)
    dominant = max(counts.items(), key=lambda kv: kv[1])
    return (dominant[0], dominant[1] / sampled, sampled)


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    declared_raw = (probe.get("lang") or "").strip().lower()
    if not declared_raw:
        # Missing lang is `structure-html-lang`'s job, not ours.
        return issues
    primary_lang = declared_raw.split("-")[0]
    expected = _LANG_TO_PRIMARY_SCRIPT.get(primary_lang)
    if not expected:
        # Unknown lang code — too noisy to fire the rule.
        return issues

    text = probe.get("text") or ""
    dominant, share, sampled = _dominant_script(text)
    if not dominant or share < _DOMINANCE_THRESHOLD:
        # Indeterminate sample — bail rather than guess.
        return issues
    if dominant == expected:
        return issues

    issues.append(make_issue(
        issue_id="structure-lang-content-mismatch",
        module="lang_detection",
        rule="structure-lang-content-mismatch",
        severity="serious",
        wcag=["3.1.1"],
        confidence="high",
        title=(
            f'<html lang="{declared_raw}"> declared but content is '
            f"predominantly {dominant} script"
        ),
        description=(
            f"The document declares lang={declared_raw!r}, which "
            f"normally implies {expected}-script content. The "
            f"page's visible text is {int(share * 100)}% "
            f"{dominant}-script characters across {sampled} "
            "alphabetic characters sampled. Screen readers will "
            "pronounce the content using the declared language's "
            "rules, which produces unintelligible audio when the "
            "actual language is different."
        ),
        selector="html",
        details={
            "declared_lang": declared_raw,
            "expected_script": expected,
            "dominant_script": dominant,
            "share": round(share, 3),
            "sample_size": sampled,
        },
        fix=(
            f"Update <html lang=...> to a code matching the actual "
            f"content language, or render this page from a "
            f"locale-aware template that emits the correct lang per "
            f"locale. Common matches for {dominant}: "
            f"{', '.join(_languages_for_script(dominant))}."
        ),
    ))
    return issues


def _languages_for_script(script: str) -> list[str]:
    return [code for code, s in _LANG_TO_PRIMARY_SCRIPT.items() if s == script][:6]


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("lang_detection probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "declared_lang": (probe or {}).get("lang", ""),
        "sample_chars": len((probe or {}).get("text") or ""),
    }
