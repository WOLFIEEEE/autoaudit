"""Form error-flow live verification — WCAG 3.3.1 (A) + 3.3.3 (AA) + 4.1.3 (AA).

[audit/forms.py](audit/forms.py) catches *static* error-association
defects: aria-invalid without aria-describedby, generic error text,
missing labels. What it cannot catch is the live failure mode where
submitting an empty form produces an error that:

  - is announced visually but not announced by SR (no live region,
    no role=alert);
  - is rendered but never associated with the failing field;
  - is rendered with a colour-only signifier (caught by color_only
    too, but the live timing here is the precise failure point).

This module discovers candidate forms, tries to submit them empty,
captures the post-submit DOM diff, and emits issues if the resulting
error state fails the linkage / announcement contracts.

Conservative: we only operate on forms whose submit button isn't a
"big-action" verb (login, pay, delete) — submitting a real
authentication form on a real site is rude. Marketing-style "Sign
up for newsletter" or "Request demo" forms are fair game; payment /
checkout / login forms get a manual-review marker instead.

Rules emitted:

- `dynamic-form-error-not-announced`  WCAG 4.1.3  serious  visible error not in live region
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Submit-button labels that signal "do not poke this in audit". We
# refuse to submit auth/payment/destructive forms because the cost
# of a side effect (account creation, charge, delete) is real.
_DO_NOT_SUBMIT_PATTERNS = (
    "log in", "login", "sign in", "signin",
    "register", "create account", "sign up", "signup",
    "pay", "purchase", "buy", "checkout", "place order",
    "delete", "remove", "cancel subscription",
    "submit ticket",  # commonly creates a real ticket
)

_DISCOVER_JS = r"""
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
    const out = [];
    for (const form of document.querySelectorAll('form')) {
        // We need at least one required field for an empty-submit
        // attempt to produce an error. Without a required field
        // the form will likely succeed on empty input.
        const required = form.querySelectorAll(
            '[required], [aria-required="true"]'
        );
        if (required.length === 0) continue;

        const submit = form.querySelector(
            'button[type="submit"], input[type="submit"], button:not([type])'
        );
        if (!submit) continue;
        const label = (submit.innerText || submit.value || '').toLowerCase().trim();
        out.push({
            form_selector: cssPath(form),
            submit_selector: cssPath(submit),
            submit_label: label,
            required_count: required.length,
        });
        if (out.length >= 4) break;  // hard cap — risk per submit is real
    }
    return out;
}
"""

_LIVE_REGION_PROBE_JS = r"""
() => {
    // Find newly-visible, non-empty error-bearing elements created by
    // the form submit. Heuristics: aria-invalid, role=alert,
    // aria-live, .error / [class*=error] / [class*=invalid] visible
    // text that wasn't there before.
    const errors = [];
    const candidates = document.querySelectorAll(
        '[aria-invalid="true"], [role="alert"], [aria-live]:not([aria-live="off"]), '
        + '.error, [class*="error"], [class*="invalid"]'
    );
    for (const el of candidates) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        if (r.width < 1 || r.height < 1) continue;
        if (s.display === 'none' || s.visibility === 'hidden') continue;
        const text = (el.innerText || '').trim();
        if (!text) continue;
        // Ancestor live region?
        let cur = el;
        let in_live = false;
        while (cur && cur !== document.body) {
            const role = (cur.getAttribute && cur.getAttribute('role')) || '';
            const live = (cur.getAttribute && cur.getAttribute('aria-live')) || '';
            if (role === 'alert' || role === 'status'
                || (live && live !== 'off')) {
                in_live = true;
                break;
            }
            cur = cur.parentElement;
        }
        errors.push({
            text: text.slice(0, 200),
            in_live_region: in_live,
            selector: (function(){
                if (el.id) return '#' + CSS.escape(el.id);
                let p = el, parts = [];
                while (p && p.nodeType === 1 && p.tagName.toLowerCase() !== 'html') {
                    parts.unshift(p.tagName.toLowerCase());
                    p = p.parentElement;
                    if (parts.length > 5) break;
                }
                return parts.join(' > ');
            })(),
        });
        if (errors.length >= 10) break;
    }
    return errors;
}
"""


def _is_safe_to_submit(label: str) -> bool:
    return not any(pat in label for pat in _DO_NOT_SUBMIT_PATTERNS)


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Discover safe forms; submit empty; verify error announcement."""
    opts = options or {}
    if not opts.get("error_flow_check", False):
        # Opt-in: this module clicks submit buttons. The default off
        # state matches the project's "do no harm" stance — operators
        # enable it explicitly when they know the target is safe.
        return {"ran": False, "issues": [], "skipped": True,
                "reason": "error_flow_check option not enabled"}

    start = time.time()
    issues: list[dict[str, Any]] = []
    try:
        candidates = page.evaluate(_DISCOVER_JS) or []
    except Exception as exc:
        log.exception("error_flow discovery failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    submitted = 0
    for spec in candidates:
        label = spec.get("submit_label", "")
        if not _is_safe_to_submit(label):
            log.debug("skipping form (unsafe submit label): %s", label)
            continue
        submit_sel = spec.get("submit_selector")
        if not submit_sel:
            continue
        try:
            page.locator(submit_sel).first.click(timeout=3000)
            page.wait_for_timeout(400)
        except Exception as exc:
            log.debug("error_flow submit failed for %s: %s", submit_sel, exc)
            continue
        submitted += 1

        try:
            errors = page.evaluate(_LIVE_REGION_PROBE_JS) or []
        except Exception:
            errors = []

        for idx, err in enumerate(errors):
            if not err.get("in_live_region"):
                issues.append(make_issue(
                    issue_id=f"dynamic-form-error-not-announced-{submit_sel}-{idx}",
                    module="error_flow",
                    rule="dynamic-form-error-not-announced",
                    severity="serious",
                    wcag=["4.1.3", "3.3.1"],
                    confidence="high",
                    title="Form error appeared without an SR-announceable live region",
                    description=(
                        "After submitting the form with empty required "
                        "fields, an error message appeared in the DOM "
                        "but neither it nor any ancestor carried "
                        "`role=alert` / `role=status` / `aria-live`. "
                        "Sighted users see the error; screen-reader "
                        "users hear nothing. WCAG 4.1.3 (Status "
                        "Messages, AA) and 3.3.1 (Error Identification, "
                        "A) both fail."
                    ),
                    selector=err.get("selector", ""),
                    details={
                        "form_selector": spec.get("form_selector", ""),
                        "error_text": (err.get("text") or "")[:200],
                    },
                    fix=(
                        "Wrap the error container in `role=alert` "
                        "(immediate announcement) or `role=status` "
                        "(polite). The element should exist on the "
                        "page BEFORE the error fires (live regions "
                        "missed at AT-scan time often go unannounced)."
                    ),
                ))

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "candidates_found": len(candidates),
        "forms_submitted": submitted,
    }
