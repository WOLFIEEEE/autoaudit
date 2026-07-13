"""Consistent Help module — WCAG 3.2.6 (A, new in 2.2).

> "If a Web page contains any of the following help mechanisms, and
> those mechanisms are repeated on multiple Web pages within a set of
> Web pages, they occur in the same order relative to other page
> content, unless a change is initiated by the user."

The recognised mechanism categories from the SC:

  - human contact details
  - human contact mechanism
  - self-help option
  - fully automated contact mechanism

We collapse them into machine-detectable kinds:

  - `contact`        — links/buttons whose accessible name matches contact-related keywords
  - `chat`           — live chat / chatbot widgets and triggers
  - `self_help`      — FAQ / help-centre / knowledge-base entries
  - `phone`          — `tel:` links
  - `email`          — `mailto:` links

Two pieces:

  1. `collect(page)` — runs per-page (called by the orchestrator inside
     `_audit_one`). Returns a list of detected mechanisms with their
     **landmark** (`header` / `main` / `footer` / `aside` / `nav` / `none`)
     and **document order index**.

  2. `analyze_cross_page(per_page)` — orchestrator calls this once after
     every page has been audited. Compares the relative order of the
     mechanisms that recur across pages. A reorder produces a single
     issue stamped against the page where the order first diverges.

The detection is best-effort. We bias towards precision (fewer false
positives) over recall: missing a mechanism is preferable to flagging
random nav links as "help" and shouting that the order changed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Order matters: the keyword list is scanned top to bottom, first
# match wins. More specific patterns precede general ones so e.g.
# "FAQ" classifies as `self_help`, not `contact`, even though the
# anchor's containing list might also include "Contact us".
_KEYWORD_KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("chat",       ("live chat", "chat with", "chat now", "start chat", "chatbot")),
    ("self_help",  ("faq", "help center", "help centre", "help hub",
                    "knowledge base", "support center", "support centre",
                    "how do i", "self-service", "self service",
                    "documentation", "user guide")),
    ("contact",    ("contact us", "contact support", "contact",
                    "talk to us", "talk to a", "get in touch",
                    "customer service", "customer support",
                    "submit a ticket", "open a ticket")),
]

_PROBE_JS = r"""
() => {
    function landmarkOf(el) {
        let cur = el;
        while (cur && cur !== document.body) {
            const role = (cur.getAttribute && cur.getAttribute('role')) || '';
            const tag  = cur.tagName ? cur.tagName.toLowerCase() : '';
            if (tag === 'header' || role === 'banner') return 'header';
            if (tag === 'footer' || role === 'contentinfo') return 'footer';
            if (tag === 'nav'    || role === 'navigation') return 'nav';
            if (tag === 'aside'  || role === 'complementary') return 'aside';
            if (tag === 'main'   || role === 'main') return 'main';
            cur = cur.parentElement;
        }
        return 'none';
    }
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
            if (parts.length > 5) break;
        }
        return parts.join(' > ');
    }
    function accessibleName(el) {
        return (
            el.getAttribute('aria-label')
            || (el.getAttribute('aria-labelledby')
                ? Array.from(el.getAttribute('aria-labelledby').split(/\s+/))
                       .map(id => (document.getElementById(id) || {}).innerText || '')
                       .join(' ')
                : '')
            || (el.innerText || '').replace(/\s+/g, ' ').trim()
            || el.getAttribute('title')
            || ''
        ).trim();
    }
    const sel = 'a[href], button, [role="button"], [role="link"]';
    const all = Array.from(document.querySelectorAll(sel));
    const out = [];
    let order = 0;
    for (const el of all) {
        // Ignore zero-size / hidden controls — they wouldn't count
        // toward "appears on the page" for a sighted or AT user.
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        if (r.width < 1 || r.height < 1) continue;
        if (s.display === 'none' || s.visibility === 'hidden') continue;
        const href = (el.getAttribute('href') || '').trim();
        const name = accessibleName(el);
        out.push({
            order: order,
            tag: el.tagName.toLowerCase(),
            href,
            accessible_name: name,
            landmark: landmarkOf(el),
            selector: cssPath(el),
            outer_html: (el.outerHTML || '').slice(0, 200),
        });
        order += 1;
    }
    return out;
}
"""


def _classify(name: str, href: str) -> str | None:
    """Return the help-kind for a candidate, or None if it doesn't match."""
    href_lower = (href or "").lower()
    if href_lower.startswith("tel:"):
        return "phone"
    if href_lower.startswith("mailto:"):
        return "email"

    name_lower = (name or "").lower()
    if not name_lower:
        return None
    for kind, keywords in _KEYWORD_KINDS:
        for kw in keywords:
            if kw in name_lower:
                return kind
    # Light fallback: an href containing /contact /support /help is a
    # strong-enough signal even when the anchor's accessible name is
    # something like "→". We require at least one of those *path*
    # segments to keep precision up.
    for path_kw, kind in (
        ("/contact", "contact"),
        ("/support", "contact"),
        ("/help", "self_help"),
        ("/faq", "self_help"),
    ):
        if path_kw in href_lower:
            return kind
    return None


def collect(page) -> dict[str, Any]:
    """Per-page collector. Returns a dict consumed by analyze_cross_page.

    Shape:
        {
          "ran": True,
          "mechanisms": [
            {"kind": "contact", "landmark": "footer", "order": 142,
             "accessible_name": "Contact us", "selector": "...", "href": ...},
            ...
          ],
          "duration_seconds": ...
        }

    The orchestrator stashes one of these per audited URL, then passes
    the collection to `analyze_cross_page` to produce 3.2.6 issues.
    """
    start = time.time()
    try:
        candidates = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("consistent_help probe failed")
        return {
            "ran": False,
            "error": str(exc),
            "mechanisms": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    mechanisms: list[dict[str, Any]] = []
    seen_kinds_by_landmark: set[tuple[str, str]] = set()
    for c in candidates:
        kind = _classify(c.get("accessible_name", ""), c.get("href", ""))
        if not kind:
            continue
        # Deduplicate within the same landmark. A footer that lists
        # "Contact us" as both an icon and a text link is one
        # mechanism, not two — taking the first preserves visual
        # reading order.
        key = (c.get("landmark", "none"), kind)
        if key in seen_kinds_by_landmark:
            continue
        seen_kinds_by_landmark.add(key)
        mechanisms.append({
            "kind": kind,
            "landmark": c.get("landmark", "none"),
            "order": int(c.get("order", 0)),
            "accessible_name": c.get("accessible_name", ""),
            "selector": c.get("selector", ""),
            "href": c.get("href", ""),
            "outer_html": c.get("outer_html", ""),
        })

    return {
        "ran": True,
        "mechanisms": mechanisms,
        "duration_seconds": round(time.time() - start, 3),
    }


def analyze_cross_page(
    per_page: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Compare relative order of help mechanisms across audited pages.

    `per_page` is `[(url, collect_result), ...]` in the order the
    orchestrator audited them. We treat the FIRST page as the baseline
    (any page can be the baseline; the user-visible "this changed
    between page A and page B" framing reads more naturally with a
    single fixed reference). Subsequent pages must preserve the
    relative order of mechanisms they share with the baseline.

    Single-page audits produce no findings — 3.2.6 is fundamentally a
    cross-page concern.
    """
    if len(per_page) < 2:
        return []

    # Per-page kind → order index, restricted to mechanisms the page
    # actually has. Mechanisms a page lacks don't constrain its order.
    page_orders: list[tuple[str, dict[str, int]]] = []
    for url, result in per_page:
        if not result or not result.get("ran"):
            continue
        kinds = {}
        for m in result.get("mechanisms", []):
            kind = m.get("kind")
            if kind and kind not in kinds:
                kinds[kind] = int(m.get("order", 0))
        if kinds:
            page_orders.append((url, kinds))

    if len(page_orders) < 2:
        return []

    baseline_url, baseline_kinds = page_orders[0]
    issues: list[dict[str, Any]] = []

    for url, kinds in page_orders[1:]:
        # Restrict to mechanisms shared between baseline and this page.
        shared = sorted(
            (k for k in kinds if k in baseline_kinds),
            key=lambda k: baseline_kinds[k],
        )
        if len(shared) < 2:
            # Need at least two shared mechanisms to detect a reorder.
            continue
        # Walk shared kinds in baseline order; verify they appear in
        # increasing document-order on the current page. Any inversion
        # is a 3.2.6 violation.
        prev_order: int | None = None
        prev_kind: str | None = None
        for kind in shared:
            this_order = kinds[kind]
            if prev_order is not None and this_order < prev_order:
                issues.append(make_issue(
                    issue_id=f"consistent-help-order-{url}-{prev_kind}-{kind}",
                    module="consistent_help",
                    rule="consistent-help-relative-order-changed",
                    severity="moderate",
                    wcag=["3.2.6"],
                    confidence="medium",
                    title=(
                        f"Help mechanisms appear in a different order on "
                        f"{url} than on {baseline_url}"
                    ),
                    description=(
                        "WCAG 3.2.6 (Consistent Help, level A, new in "
                        "WCAG 2.2) requires that help mechanisms which "
                        "repeat across a set of pages appear in the "
                        f"same relative order. On {baseline_url} "
                        f"the {prev_kind!r} mechanism comes before "
                        f"{kind!r}, but on {url} they appear in the "
                        "opposite order. Users who learn the position "
                        "of help on one page expect to find it in the "
                        "same place on the next."
                    ),
                    details={
                        "baseline_url": baseline_url,
                        "page_url": url,
                        "shared_mechanisms": shared,
                        "baseline_order": [
                            (k, baseline_kinds[k]) for k in shared
                        ],
                        "page_order": [
                            (k, kinds[k]) for k in shared
                        ],
                    },
                    fix=(
                        "Move the page header / footer help block to a "
                        "shared layout component so the relative order "
                        "is identical on every page. Detection is "
                        "heuristic — verify the diff is intentional "
                        "before re-templating."
                    ),
                ))
                # One reorder per page is enough — listing every pairwise
                # inversion would just be noise on heavily reordered pages.
                break
            prev_order = this_order
            prev_kind = kind

    return issues
