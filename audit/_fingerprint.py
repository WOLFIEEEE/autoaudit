"""Stable fingerprints for audit issues.

Two problems this module solves:

1. **Dedup blindness.** Two modules flagging the same DOM element often
   produce two different selector strings (`#root > div.x > button`
   vs `button.cta` vs `[data-testid="submit"]`). The old dedup keyed on
   raw selectors, so cross-module twins slipped through. A fingerprint
   normalizes selector + rule into a stable key.

2. **No diffing between audit runs.** Positional IDs like
   `axe-image-alt-0` change when the page reflows or a new violation
   is inserted earlier, so `audit N` vs `audit N-1` can't be diffed
   reliably. Fingerprints derived from stable fields (rule + normalized
   selector + snippet hash + WCAG tuple) let `audit_diff.py` answer
   "what got fixed? what regressed? what's new?" across runs.

Fingerprints are best-effort — they depend on the DOM returning roughly
the same selector shape across runs. Where a rule is inherently
page-level (no element: "structure-title-missing"), the fingerprint
keys on `rule + url`.

All functions here are pure Python, safe to run off the browser thread.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Positional noise we remove before hashing a selector.
# These are the patterns that change between runs without any real
# content change — nth-of-type indices shift when a sibling is added,
# CSS-in-JS hashes rotate on every build, random data-react-ids mutate
# on hydration.
_NTH_RE = re.compile(r":nth-of-type\(\d+\)")
_HASH_ID_RE = re.compile(r"#(?:css|mui|chakra|sc|emotion|styled)-[A-Za-z0-9_-]+", re.IGNORECASE)
_REACT_ATTR_RE = re.compile(r'\[data-react[^\]]*\]')


def normalize_selector(selector: str | None) -> str:
    """Strip positional/auto-generated noise from a selector.

    Leaves stable hooks intact (real IDs, test-ids, name= attributes).
    Used to key fingerprints so two modules' different synthesized
    selectors for the same element collapse together.
    """
    if not selector:
        return ""
    s = selector.strip()
    s = _NTH_RE.sub("", s)
    s = _HASH_ID_RE.sub("", s)
    s = _REACT_ATTR_RE.sub("", s)
    # Collapse runs of whitespace and combinators that the substitutions
    # above may have left dangling ("foo >  > bar" → "foo > bar").
    s = re.sub(r"\s*>\s*>\s*", " > ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" >")


def _snippet_hash(snippet: str | None) -> str:
    """Short, stable hash of an HTML snippet.

    Snippets carry tag+attrs+a bit of children — hashing them lets two
    findings that synthesized different selectors but point at the same
    markup converge on the same fingerprint. We truncate to 12 hex chars
    because the fingerprint is combined with other fields; longer doesn't
    add useful entropy.
    """
    if not snippet:
        return ""
    # Normalize whitespace so hash isn't sensitive to pretty-printing.
    s = re.sub(r"\s+", " ", snippet).strip()
    return hashlib.sha1(
        s.encode("utf-8", errors="ignore"), usedforsecurity=False
    ).hexdigest()[:12]


def issue_fingerprint(
    *,
    rule: str,
    selector: str | None = None,
    html_snippet: str | None = None,
    wcag_criteria: list[str] | None = None,
    page_url: str | None = None,
) -> str:
    """Deterministic 16-hex-char fingerprint for a single issue.

    Combines rule, normalized selector, snippet hash, and sorted WCAG
    criteria. Same element + same rule across runs → same fingerprint
    even if the raw selector shifted (new sibling, CSS hash rotated).

    `page_url` is folded in only when selector+snippet are both empty
    (a page-level rule like "html-lang-missing") so those findings
    still dedupe correctly across a multi-page audit.
    """
    norm_sel = normalize_selector(selector)
    snip = _snippet_hash(html_snippet)
    wcag_part = ",".join(sorted(wcag_criteria or ()))

    if not norm_sel and not snip and page_url:
        # Page-level finding — key on url so the same rule firing on
        # two different pages doesn't collapse into one fingerprint.
        basis = f"{rule}|page:{page_url}|{wcag_part}"
    else:
        basis = f"{rule}|{norm_sel}|{snip}|{wcag_part}"

    return hashlib.sha1(
        basis.encode("utf-8", errors="ignore"), usedforsecurity=False
    ).hexdigest()[:16]


def fingerprint_for_issue(issue: dict[str, Any], page_url: str | None = None) -> str:
    """Convenience wrapper that reads the usual issue-dict fields."""
    el = issue.get("element") or {}
    return issue_fingerprint(
        rule=issue.get("rule", ""),
        selector=el.get("selector"),
        html_snippet=el.get("html_snippet"),
        wcag_criteria=issue.get("wcag_criteria"),
        page_url=page_url or issue.get("page_url"),
    )
