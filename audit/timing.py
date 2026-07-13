"""Timing Adjustable — WCAG 2.2.1 (A).

WCAG 2.2.1 says: for each time limit set by the content, the user must
be able to turn it off, adjust it, or extend it (with exceptions for
real-time events, essential limits, and limits longer than 20 hours).

Most time limits that matter for 2.2.1 are *server-driven* session
timeouts, which a page audit cannot see — the registry is honest about
that and keeps 2.2.1 at the `partial` tier. But one common, purely
client-side time limit IS detectable with high precision:

  - **`<meta http-equiv="refresh" content="N">`** — a client-side timer
    that reloads the page (when there is no URL) or auto-redirects to
    another URL (`content="N; url=..."`) after N seconds. The classic
    2.2.1 failure is a short auto-refresh / timed redirect the user
    cannot pause, extend, or turn off. WCAG technique F40 (timed
    redirect) and F41 (timed refresh) describe exactly this.

We flag any `meta refresh` with a finite delay, and raise the severity
for short delays (<= 20s) where a user is most likely to lose work or be
interrupted mid-task. A `content="0; url=..."` instant redirect is
called out separately (F40) — it's not a "time limit" the user can act
on at all, which is its own 2.2.1 / 2.4 concern.

The analyzer is a pure function over a JS-extracted snapshot, so unit
tests don't need a browser.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Parse the `content` attribute of a meta-refresh:
#   "5"                       -> refresh after 5s
#   "5; url=https://x"        -> redirect to url after 5s
#   "0;url=/next"             -> instant redirect
# The delay is the leading number; an optional url= follows a separator.
_REFRESH_CONTENT = re.compile(
    r"""^\s*(?P<delay>\d+(?:\.\d+)?)\s*
        (?:[;,]\s*url\s*=\s*['"]?(?P<url>[^'"]+?)['"]?\s*)?$""",
    re.IGNORECASE | re.VERBOSE,
)

# Delays at or below this (seconds) are treated as a serious time limit:
# short enough to interrupt a user mid-read or mid-task.
SHORT_DELAY_S = 20.0


def _parse_refresh(content: str) -> dict[str, Any] | None:
    """Return {'delay': float, 'url': str|None} or None if not a refresh."""
    if not content:
        return None
    m = _REFRESH_CONTENT.match(content.strip())
    if not m:
        return None
    try:
        delay = float(m.group("delay"))
    except (TypeError, ValueError):
        return None
    return {"delay": delay, "url": (m.group("url") or "").strip() or None}


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure analysis over the probe snapshot. No browser required."""
    issues: list[dict[str, Any]] = []

    for idx, item in enumerate(probe.get("meta_refresh") or []):
        parsed = _parse_refresh(item.get("content") or "")
        if parsed is None:
            continue
        delay = parsed["delay"]
        url = parsed["url"]
        is_redirect = url is not None
        instant = delay == 0

        if instant and is_redirect:
            # F40 instant timed redirect — not user-adjustable at all.
            severity = "serious"
            rule = "timing-meta-refresh-redirect"
            title = "Instant meta-refresh redirect (no user control)"
            desc = (
                f"This page uses <meta http-equiv=\"refresh\" "
                f"content=\"{item.get('content')}\"> to redirect to "
                f"{url!r} immediately. A client-side timed redirect the "
                "user cannot pause, extend, or cancel fails WCAG 2.2.1 "
                "(A) (technique F40). Use a server-side 3xx redirect "
                "instead, or a normal link the user activates."
            )
        elif is_redirect:
            severity = "serious" if delay <= SHORT_DELAY_S else "moderate"
            rule = "timing-meta-refresh-redirect"
            title = "Timed meta-refresh redirect may not be user-adjustable"
            desc = (
                f"This page auto-redirects to {url!r} after {delay:g}s via "
                f"<meta http-equiv=\"refresh\">. Unless the user can turn "
                "off, adjust, or extend this delay, it fails WCAG 2.2.1 "
                "(A) (technique F40) — users who read slowly or rely on "
                "assistive tech can be navigated away mid-task."
            )
        else:
            severity = "serious" if delay <= SHORT_DELAY_S else "moderate"
            rule = "timing-meta-refresh"
            title = "Timed meta-refresh reload may not be user-adjustable"
            desc = (
                f"This page reloads itself every {delay:g}s via "
                f"<meta http-equiv=\"refresh\">. A periodic refresh the "
                "user cannot pause, extend, or turn off fails WCAG 2.2.1 "
                "(A) (technique F41) — it resets scroll position and "
                "focus, disrupting screen-reader and keyboard users."
            )

        # Shared fields; only the literal rule id differs by branch.
        # Passing rule= as a literal (not the `rule` variable) keeps the
        # ids discoverable by the AST orphan-check in
        # tests/test_rule_versions.py.
        common = dict(
            issue_id=f"{rule}-{idx}",
            module="timing",
            severity=severity,
            wcag=["2.2.1"],
            confidence="high",
            title=title,
            description=desc,
            selector=item.get("selector", "meta[http-equiv='refresh']"),
            html_snippet=item.get("html", ""),
            details={
                "content": item.get("content", ""),
                "delay_seconds": delay,
                "redirect_url": url,
                "instant": instant,
            },
            fix=(
                "Remove the meta refresh. For navigation, use a "
                "server-side redirect or a user-activated link. If a "
                "timed update is essential, give the user a way to turn "
                "it off, adjust the interval, or extend it (a 'keep me "
                "here' control), and warn before acting."
            ),
        )
        if is_redirect:
            issues.append(make_issue(rule="timing-meta-refresh-redirect", **common))
        else:
            issues.append(make_issue(rule="timing-meta-refresh", **common))

    return issues


# Probe: collect every <meta http-equiv="refresh"> content string.
# Reads attributes only — no execution.
_PROBE_JS = r"""
() => {
    const meta_refresh = [];
    const metas = document.querySelectorAll('meta[http-equiv]');
    for (const m of metas) {
        const he = (m.getAttribute('http-equiv') || '').trim().toLowerCase();
        if (he !== 'refresh') continue;
        meta_refresh.push({
            content: m.getAttribute('content') || '',
            html: (m.outerHTML || '').slice(0, 200),
            selector: "meta[http-equiv='refresh']",
        });
        if (meta_refresh.length >= 20) break;
    }
    return {meta_refresh};
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("timing probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "meta_refresh_candidates": len((probe or {}).get("meta_refresh") or []),
    }
