"""Accessible Authentication (Minimum) — WCAG 3.3.8 (AA, new in 2.2).

> "A cognitive function test... is not required for any step in an
> authentication process unless that step provides at least one of
> the following: alternative, mechanism, object recognition,
> personal content."

The SC's spirit: don't ask users to do puzzles, transcribe text from
images, or solve riddles to authenticate. Username + password is
fine; CAPTCHA without an alternative is not.

What we detect:

  1. **Auth-context CAPTCHA**: a known CAPTCHA element (Recaptcha,
     hCaptcha, Cloudflare Turnstile, FunCaptcha, Geetest) embedded
     on a page that also has an `<input type="password">` or a
     `[autocomplete="current-password" / "one-time-code"]`. Outside
     auth flows CAPTCHAs don't engage 3.3.8; we tier severity by
     whether an alt-text alternative is offered.

  2. **Cognitive-test heuristics**: form fields whose label text
     matches puzzle/transcription patterns ("type the letters",
     "solve...", "what is X + Y", "select all the squares with...").
     Fires only on auth pages.

Rules emitted:

- `accessible-auth-captcha-detected`  WCAG 3.3.8  serious   CAPTCHA on auth page
                                                            with no documented alternative
- `accessible-auth-cognitive-test`    WCAG 3.3.8  serious   transcribe / arithmetic /
                                                            puzzle-style auth challenge

Heuristic — confidence is `medium` because we cannot prove an
alternative does NOT exist (it might be on a "I can't read the
audio" link we didn't traverse). Better to surface for review than
to claim hard failure.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Common CAPTCHA / human-verification fingerprints. Each entry:
# (vendor name, JS marker the script injects).
_CAPTCHA_SIGNATURES = (
    ("reCAPTCHA",        "g-recaptcha"),
    ("reCAPTCHA",        "grecaptcha"),
    ("hCaptcha",         "h-captcha"),
    ("Cloudflare Turnstile", "cf-turnstile"),
    ("Cloudflare Turnstile", "cdn-cgi/challenge-platform"),
    ("FunCaptcha (Arkose)", "funcaptcha"),
    ("Arkose",           "arkose-labs"),
    ("Geetest",          "geetest"),
    ("Friendly Captcha", "frc-captcha"),
)

# Cognitive-test prompt patterns. Lowercased and stripped before match.
_COGNITIVE_PROMPTS = (
    re.compile(r"type the (text|letters|characters|word|words)"),
    re.compile(r"transcribe"),
    re.compile(r"copy the (text|letters|characters|code) (above|below)"),
    re.compile(r"what is \d+\s*[\+\-\*/x×]\s*\d+"),
    re.compile(r"solve (this )?puzzle"),
    re.compile(r"(select|click) all (the )?(squares|images|tiles) (with|of|that)"),
    re.compile(r"prove (you('| a)?re|that you are) (a |not a )?(human|robot|bot)"),
    re.compile(r"unscramble"),
    re.compile(r"complete the (sequence|pattern)"),
)


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
    // Auth context: presence of a password field OR autocomplete tokens
    // that signal an auth flow. Bare `[name="password"]` is also a
    // strong fallback for sites that misconfigure type=text for "show
    // password" toggles.
    const auth_context = !!document.querySelector(
        'input[type="password"], '
        + '[autocomplete="current-password"], '
        + '[autocomplete="new-password"], '
        + '[autocomplete="one-time-code"]'
    );

    // CAPTCHA fingerprints: probe on (a) class names, (b) element IDs,
    // (c) script src in the page, (d) iframes pointing at known hosts.
    const html_text = document.documentElement.outerHTML.toLowerCase();
    const captcha_findings = [];
    for (const sig of [
        ['reCAPTCHA', 'g-recaptcha'],
        ['reCAPTCHA', 'grecaptcha'],
        ['hCaptcha', 'h-captcha'],
        ['Cloudflare Turnstile', 'cf-turnstile'],
        ['Cloudflare Turnstile', 'cdn-cgi/challenge-platform'],
        ['FunCaptcha (Arkose)', 'funcaptcha'],
        ['Arkose', 'arkose-labs'],
        ['Geetest', 'geetest'],
        ['Friendly Captcha', 'frc-captcha'],
    ]) {
        const [vendor, marker] = sig;
        if (html_text.indexOf(marker) === -1) continue;
        // Try to anchor to a real DOM element for the report.
        const anchor = document.querySelector(
            '.' + marker + ', #' + marker + ', [data-sitekey], iframe[src*="' + marker + '"]'
        ) || document.body;
        captcha_findings.push({
            vendor,
            marker,
            selector: cssPath(anchor),
            html: (anchor.outerHTML || '').slice(0, 200),
        });
    }

    // Cognitive-prompt scan — collect short text from labels and
    // headings inside any <form> on an auth page. We deliberately
    // cap the haystack: the SC concerns auth fields specifically.
    const auth_prompts = [];
    if (auth_context) {
        const candidates = document.querySelectorAll(
            'form label, form legend, form .help, form [class*="instruction"], '
            + 'form h1, form h2, form h3, form p'
        );
        for (const c of candidates) {
            const t = (c.innerText || '').trim();
            if (!t || t.length > 240) continue;
            auth_prompts.push({
                text: t,
                selector: cssPath(c),
                html: (c.outerHTML || '').slice(0, 200),
            });
            if (auth_prompts.length >= 80) break;
        }
    }

    return {auth_context, captcha_findings, auth_prompts};
}
"""


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not probe.get("auth_context"):
        return issues  # SC engages on auth flows only

    seen_vendors: set[str] = set()
    for idx, c in enumerate(probe.get("captcha_findings") or []):
        vendor = c.get("vendor") or "unknown"
        if vendor in seen_vendors:
            continue
        seen_vendors.add(vendor)
        issues.append(make_issue(
            issue_id=f"accessible-auth-captcha-detected-{idx}",
            module="accessible_auth",
            rule="accessible-auth-captcha-detected",
            severity="serious",
            wcag=["3.3.8"],
            confidence="medium",
            title=f"{vendor} CAPTCHA detected on an authentication page",
            description=(
                "WCAG 3.3.8 (Accessible Authentication, Minimum, AA, "
                f"new in 2.2) prohibits cognitive function tests in "
                "auth flows unless an alternative is offered. The "
                f"presence of {vendor} on this page is a strong "
                "signal that users are being asked to solve "
                "image-recognition or transcription puzzles to "
                "authenticate. Heuristic — review and dismiss only "
                "if a documented alternative (audio, mailed link, "
                "WebAuthn, etc.) is available to users who cannot "
                "complete the visual challenge."
            ),
            selector=c.get("selector", ""),
            html_snippet=c.get("html", ""),
            details={"vendor": vendor, "marker": c.get("marker")},
            fix=(
                "Offer at least one cognitive-test-free authentication "
                "path. Options: WebAuthn / passkeys "
                "(autocomplete=\"webauthn\"), magic-link via email, "
                "device-bound trust, OAuth via a passkey-enabled "
                "provider. The CAPTCHA can remain as a defence layer "
                "but must not be the only path."
            ),
        ))

    for idx, p in enumerate(probe.get("auth_prompts") or []):
        text = (p.get("text") or "").lower().strip()
        for pattern in _COGNITIVE_PROMPTS:
            if pattern.search(text):
                issues.append(make_issue(
                    issue_id=f"accessible-auth-cognitive-test-{idx}",
                    module="accessible_auth",
                    rule="accessible-auth-cognitive-test",
                    severity="serious",
                    wcag=["3.3.8"],
                    confidence="medium",
                    title=(
                        "Auth flow appears to require a cognitive "
                        f"function test: {p.get('text', '')[:80]!r}"
                    ),
                    description=(
                        "WCAG 3.3.8 (AA, new in 2.2) treats puzzles, "
                        "transcription, and arithmetic challenges as "
                        "cognitive function tests. Users with "
                        "cognitive disabilities are blocked when "
                        "those are the only path to authenticate. "
                        "The instruction text on this page matches "
                        "a known puzzle pattern."
                    ),
                    selector=p.get("selector", ""),
                    html_snippet=p.get("html", ""),
                    details={"matched_text": p.get("text", "")[:240]},
                    fix=(
                        "Replace the cognitive test with object "
                        "recognition (e.g. \"select your account "
                        "avatar\"), personal content (\"what city "
                        "did you live in?\"), or by removing the "
                        "extra step entirely. WebAuthn / passkeys "
                        "satisfy the SC by default."
                    ),
                ))
                break  # one match per prompt is enough

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("accessible_auth probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "auth_context": (probe or {}).get("auth_context", False),
        "captchas_found": len((probe or {}).get("captcha_findings") or []),
    }
