"""Skip-link module — WCAG 2.4.1 Bypass Blocks (A).

Static analyses typically just check whether a `<a href="#main">`-style
link exists. That misses the much more common failure: a skip link is
declared but **does not work** — pressing Tab + Enter doesn't move
focus to the target, because:

  - the target id doesn't exist,
  - the target exists but isn't focusable (`<div id="main">` with no
    tabindex; activating the link scrolls but leaves focus on `<a>`),
  - JS intercepts the click and prevents the default,
  - the link is `display: none` until focus, which is intentional, but
    we still need to verify the *focused* state behaves correctly.

This module:

  1. Inventories candidate skip links — `<a href^="#">` whose
     accessible name matches a "skip to..." pattern.
  2. For each, focuses it explicitly, presses Enter, and checks that
     focus moves to a programmatically-focusable element matching the
     target hash.

Rules emitted:

- `skiplink-missing`              WCAG 2.4.1  serious   no skip link found
- `skiplink-target-missing`       WCAG 2.4.1  serious   target id absent in DOM
- `skiplink-target-not-focusable` WCAG 2.4.1  serious   target lacks tabindex / is non-focusable
- `skiplink-broken`               WCAG 2.4.1  serious   activating the link does not move focus to the target
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Match common skip-link wording. The list is intentionally narrow —
# random anchor links named "Top" or "Back" aren't skip links.
_SKIP_PATTERNS = (
    "skip to", "skip navigation", "skip nav", "skip past",
    "jump to", "go to main",
)

_FIND_JS = r"""
() => {
    function visible(el) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        // A skip link is often visually hidden until focus, so we
        // cannot require visible() to consider it a candidate. We
        // simply need it in the DOM and not aria-hidden.
        if (el.getAttribute('aria-hidden') === 'true') return false;
        return s.display !== 'none' || true;  // accept off-screen sr-only
    }
    function name(el) {
        return (
            el.getAttribute('aria-label')
            || (el.innerText || '').trim()
            || el.getAttribute('title')
            || ''
        ).trim();
    }
    const candidates = [];
    for (const a of document.querySelectorAll('a[href^="#"]')) {
        if (!visible(a)) continue;
        const href = a.getAttribute('href') || '';
        const target_id = href.startsWith('#') ? href.slice(1) : '';
        if (!target_id) continue;
        const txt = name(a).toLowerCase();
        candidates.push({
            text: name(a),
            text_lower: txt,
            target_id,
            selector: '#' + (a.id ? CSS.escape(a.id) : '') ,
            href,
        });
    }
    return candidates;
}
"""

_TARGET_PROBE_JS = r"""
(target_id) => {
    const t = document.getElementById(target_id);
    if (!t) return {exists: false};
    const ti = t.getAttribute('tabindex');
    // Programmatically focusable: native focusable tag OR explicit
    // tabindex (including "-1", which makes it script-focusable).
    const tag = t.tagName.toLowerCase();
    const nativeFocusable = ['a','button','input','select','textarea',
                             'iframe','main','section','article','aside',
                             'nav','footer','header'].includes(tag);
    const explicitTabindex = ti !== null && ti !== undefined;
    return {
        exists: true,
        tabindex: ti,
        tag: tag,
        native_focusable: nativeFocusable,
        explicitly_focusable: explicitTabindex,
    };
}
"""

_FOCUSED_MATCHES_JS = r"""
(target_id) => {
    const t = document.getElementById(target_id);
    if (!t) return false;
    const focused = document.activeElement;
    if (!focused) return false;
    // Pass if focus is on the target itself, or any descendant
    // (some teams target the heading inside <main>).
    return focused === t || t.contains(focused);
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    issues: list[dict[str, Any]] = []
    try:
        candidates = page.evaluate(_FIND_JS)
    except Exception as exc:
        log.exception("skiplinks discovery failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    skip_links = [
        c for c in (candidates or [])
        if any(pat in (c.get("text_lower") or "") for pat in _SKIP_PATTERNS)
    ]

    if not skip_links:
        # Page-level finding — fingerprint at the page rather than at a
        # specific element so the dedup pass treats it as a singleton.
        issues.append(make_issue(
            issue_id="skiplink-missing",
            module="skiplinks",
            rule="skiplink-missing",
            severity="serious",
            wcag=["2.4.1"],
            confidence="medium",
            title="No skip link found",
            description=(
                "WCAG 2.4.1 (Bypass Blocks, level A) requires a "
                "mechanism for keyboard users to skip past repeated "
                "blocks of content (header, nav). No anchor whose "
                "accessible name matches common skip-link patterns "
                "('skip to...', 'jump to...') was detected on the "
                "page. Keyboard-only and screen-reader users have to "
                "tab through every header link on every page load."
            ),
            fix=(
                "Add `<a href=\"#main-content\">Skip to main content</a>` "
                "as the first focusable element. The target must be "
                "programmatically focusable (e.g. a `<main id=\"main-content\" "
                "tabindex=\"-1\">`) so activating the link moves focus."
            ),
        ))
        return {
            "ran": True,
            "issues": issues,
            "duration_seconds": round(time.time() - start, 3),
            "candidates_examined": len(candidates or []),
            "skip_links_found": 0,
        }

    # Verify each candidate works. We cap at the first 3 — multiple
    # skip links exist on some pages but if the first three fail, that
    # message is loud enough; checking 50 is busywork.
    for idx, link in enumerate(skip_links[:3]):
        target_id = link.get("target_id", "")
        try:
            target = page.evaluate(_TARGET_PROBE_JS, target_id)
        except Exception as exc:
            log.debug("skiplink target probe failed: %s", exc)
            continue
        if not target.get("exists"):
            issues.append(make_issue(
                issue_id=f"skiplink-target-missing-{idx}",
                module="skiplinks",
                rule="skiplink-target-missing",
                severity="serious",
                wcag=["2.4.1"],
                confidence="high",
                title=f"Skip link {link.get('text', '')!r} points to a missing id",
                description=(
                    f"The skip link targets #{target_id}, but no element "
                    "with that id exists in the DOM. Activating the link "
                    "is a no-op — the bypass mechanism is broken."
                ),
                details={"target_id": target_id, "link_text": link.get("text", "")},
                fix=(
                    f"Add an element with id=\"{target_id}\" at the start "
                    "of the main content region, and make it focusable "
                    "(tabindex=\"-1\" is sufficient for a `<main>`)."
                ),
            ))
            continue
        if not (target.get("native_focusable") or target.get("explicitly_focusable")):
            issues.append(make_issue(
                issue_id=f"skiplink-target-not-focusable-{idx}",
                module="skiplinks",
                rule="skiplink-target-not-focusable",
                severity="serious",
                wcag=["2.4.1"],
                confidence="high",
                title=f"Skip link target #{target_id} is not focusable",
                description=(
                    f"The element with id=\"{target_id}\" exists but is "
                    "not programmatically focusable. Browsers will "
                    "scroll to it when the skip link is activated, "
                    "but focus stays on the skip link itself, so the "
                    "user's next Tab returns to the next header link "
                    "and the bypass fails."
                ),
                details={"target_id": target_id, "tag": target.get("tag")},
                fix=(
                    f"Add tabindex=\"-1\" to the target element "
                    f"(`<{target.get('tag')} id=\"{target_id}\" "
                    "tabindex=\"-1\">`) so focus can move there "
                    "without making it tabbable in normal navigation."
                ),
            ))
            continue

        # Live verification: focus the link, press Enter, check focus.
        try:
            anchor = page.locator(f'a[href="#{target_id}"]').first
            anchor.scroll_into_view_if_needed(timeout=2000)
            anchor.focus(timeout=2000)
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)
            moved = page.evaluate(_FOCUSED_MATCHES_JS, target_id)
        except Exception as exc:
            log.debug("skiplink activation failed for %s: %s", target_id, exc)
            moved = None

        if moved is False:
            issues.append(make_issue(
                issue_id=f"skiplink-broken-{idx}",
                module="skiplinks",
                rule="skiplink-broken",
                severity="serious",
                wcag=["2.4.1"],
                confidence="high",
                title=f"Skip link {link.get('text', '')!r} does not move focus",
                description=(
                    "The link is structured correctly (target exists "
                    "and is focusable), but pressing Enter while it is "
                    "focused does not move focus to the target. Common "
                    "causes: a JS click handler calls preventDefault "
                    "without moving focus, or a SPA router intercepts "
                    "the navigation. Keyboard users still have to tab "
                    "through every preceding header link."
                ),
                details={"target_id": target_id, "link_text": link.get("text", "")},
                fix=(
                    "If a click handler intercepts the navigation, "
                    "have it call `target.focus()` after preventing "
                    "default. Otherwise let the browser handle the "
                    "fragment-navigation natively."
                ),
            ))

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "candidates_examined": len(candidates or []),
        "skip_links_found": len(skip_links),
        "skip_links_verified": min(3, len(skip_links)),
    }
