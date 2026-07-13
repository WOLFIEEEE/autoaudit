"""Live regions module — WCAG 4.1.3 Status Messages (AA).

Two complementary checks:

  1. **Inventory**       — enumerate every element that *would* be a
                           live region for assistive tech (`aria-live`,
                           `role=alert/status/log/marquee/timer`, the
                           HTML5 live-region tags). Surface the count
                           in the result so reviewers can see at a
                           glance how many live regions the page
                           actually has — most pages claim 0 when in
                           fact they have several stale ones.
  2. **Misconfiguration** — flag the patterns that look like a live
                           region but won't actually announce:
                             - `role="alert"` paired with
                               `aria-live="polite"` (the role's
                               implicit assertive politeness is
                               overridden, which is rarely intended);
                             - `role="status"` with
                               `aria-live="off"` (silenced);
                             - empty live regions
                               (`<div role="alert"></div>` shipping in
                               static HTML is a smell — most ATs
                               announce only on text *changes* from a
                               non-empty initial state, so the first
                               actual update will not fire).

This module deliberately does NOT try to *prove* a status message
fires — that needs an interaction (declared via the dynamic DSL),
which is handled there. The goal is to catch mis-configured static
markup that the page author probably intended to use.

Rules emitted:

- `live-region-role-conflict`     WCAG 4.1.3  serious   role=alert + aria-live=polite, or similar contradiction
- `live-region-silenced`          WCAG 4.1.3  serious   role implies live region but aria-live="off" silences it
- `live-region-empty-on-load`     WCAG 4.1.3  moderate  live region present but empty in the initial HTML (latent risk)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Role → implicit aria-live politeness. When an explicit aria-live
# value disagrees with the implicit politeness of the role, that's
# either intentional (and rare) or a bug.
_ROLE_IMPLICIT_LIVE = {
    "alert": "assertive",
    "alertdialog": "assertive",
    "log": "polite",
    "marquee": "off",
    "status": "polite",
    "timer": "off",
}

_LIVE_ROLES = frozenset(_ROLE_IMPLICIT_LIVE)

_PROBE_JS = r"""
() => {
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
    const sel = '[aria-live], [role="alert"], [role="alertdialog"], '
              + '[role="log"], [role="marquee"], [role="status"], '
              + '[role="timer"], output';
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
        const role = (el.getAttribute('role') || '').toLowerCase().trim();
        const aria_live = (el.getAttribute('aria-live') || '').toLowerCase().trim();
        const aria_atomic = el.getAttribute('aria-atomic');
        const aria_relevant = el.getAttribute('aria-relevant');
        const text = (el.textContent || '').trim();
        out.push({
            tag: el.tagName.toLowerCase(),
            role,
            aria_live,
            aria_atomic: aria_atomic === null ? null : aria_atomic,
            aria_relevant: aria_relevant === null ? null : aria_relevant,
            initial_text_length: text.length,
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
        });
    }
    return out;
}
"""


def analyze(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for idx, r in enumerate(regions):
        role = r.get("role", "")
        aria_live = r.get("aria_live", "")
        selector = r.get("selector", "")
        html = r.get("html", "")

        # 1. Conflict between role and explicit aria-live.
        implicit = _ROLE_IMPLICIT_LIVE.get(role)
        if implicit and aria_live and aria_live != implicit:
            # Special case: role=alert + aria-live=off is "silenced",
            # which is a stronger failure than a politeness mismatch.
            if aria_live == "off":
                issues.append(make_issue(
                    issue_id=f"live-region-silenced-{idx}",
                    module="live_regions",
                    rule="live-region-silenced",
                    severity="serious",
                    wcag=["4.1.3"],
                    confidence="high",
                    title=(
                        f'role="{role}" element has aria-live="off"; '
                        "updates will not be announced"
                    ),
                    description=(
                        f"This element declares role={role!r}, which "
                        "would normally announce its content as a "
                        "status message. The explicit aria-live=\"off\" "
                        "overrides the role's implicit live politeness "
                        "and silences it entirely — assistive tech "
                        "ignores updates."
                    ),
                    selector=selector,
                    html_snippet=html,
                    details={"role": role, "aria_live": aria_live},
                    fix=(
                        'Remove aria-live="off" (the role provides the '
                        f'correct {implicit!r} politeness implicitly), '
                        'or change the role to match the desired '
                        'silence.'
                    ),
                ))
            else:
                issues.append(make_issue(
                    issue_id=f"live-region-role-conflict-{idx}",
                    module="live_regions",
                    rule="live-region-role-conflict",
                    severity="moderate",
                    wcag=["4.1.3"],
                    confidence="high",
                    title=(
                        f'role="{role}" + aria-live="{aria_live}" '
                        f"contradicts the role's implicit "
                        f"{implicit!r} politeness"
                    ),
                    description=(
                        f"role={role!r} implies aria-live={implicit!r}; "
                        f"the explicit aria-live={aria_live!r} overrides "
                        "that. Some assistive tech respects the role, "
                        "some respects the explicit value, so the "
                        "announcement behaviour is inconsistent. This "
                        "almost always indicates the author meant one "
                        "or the other, not both."
                    ),
                    selector=selector,
                    html_snippet=html,
                    details={
                        "role": role,
                        "aria_live": aria_live,
                        "implicit_aria_live": implicit,
                    },
                    fix=(
                        "Pick one source of truth: either remove the "
                        f"explicit aria-live (the role implies "
                        f"{implicit!r}), or change the role to one "
                        f"whose implicit politeness matches the "
                        "intended behaviour."
                    ),
                ))
        elif aria_live == "off" and role in _LIVE_ROLES:
            # Already handled in the conflict branch above, but kept
            # here as a no-op fallthrough for readability.
            pass

        # 2. Empty live region in initial HTML.
        # We only flag this for live-region roles, not bare aria-live.
        # `<div aria-live="polite"></div>` is the canonical pattern
        # for "I will fill this later" — still latent risk but
        # legitimate when followed by a JS write that the orchestrator
        # cannot observe statically.
        if (role in _LIVE_ROLES) and r.get("initial_text_length", 0) == 0:
            issues.append(make_issue(
                issue_id=f"live-region-empty-on-load-{idx}",
                module="live_regions",
                rule="live-region-empty-on-load",
                severity="moderate",
                wcag=["4.1.3"],
                confidence="medium",
                title=(
                    f'role="{role}" element is empty at page load'
                ),
                description=(
                    "This element is configured as a live region but "
                    "ships empty in the initial HTML. Most assistive "
                    "tech announces *changes* to a live region's "
                    "content. NVDA and JAWS in particular often miss "
                    "the very first update if the region was empty "
                    "when the AT first scanned the page — the change "
                    "from empty-to-something is treated as initial "
                    "render, not as a status update."
                ),
                selector=selector,
                html_snippet=html,
                details={"role": role, "aria_live": aria_live or implicit},
                fix=(
                    "Either pre-populate the region with a placeholder "
                    "(e.g. \"Status: ready.\") and update its text on "
                    "events, or use a separate hidden live region per "
                    "message instead of mutating one shared region. "
                    "Verify with `dynamic.py` interactions that the "
                    "first announcement actually fires."
                ),
            ))

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        regions = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("live_regions probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(regions or [])
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "regions_found": len(regions or []),
        "regions": regions or [],
    }
