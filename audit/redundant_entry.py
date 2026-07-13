"""Redundant Entry — WCAG 3.3.7 (A, new in 2.2).

> "Information previously entered by or provided to the user that is
> required to be entered again in the same process is either:
> auto-populated, or available for the user to select."

Real automation of this needs a multi-step interaction recorder — we
follow the user from step 1 to step N, observe what was entered,
then diff against what step N+1 prompts for. That's a substantial
build (week+) and isn't yet wired up.

What we *can* do statically with high precision:

  1. **Single-page heuristic**: when the same `name` (or `name`-like
     attribute combo) appears on multiple inputs across multiple
     forms on the same page WITHOUT any field carrying `autocomplete=`,
     the design is fragile. Multi-step flows that prompt for the
     same data twice are usually the next step on this page.

  2. **Known wizard patterns**: pages with a `[role="progressbar"]`
     or a `<ol>`-styled step indicator AND a form. Surface a manual-
     review nudge: "this looks like a multi-step flow; verify it
     auto-fills the user's prior entries."

Rules emitted:

- `redundant-entry-no-autocomplete`  WCAG 3.3.7  moderate

Heuristic — the rule is medium-confidence and pairs with the manual-
review entry in [audit/wcag_coverage.py](audit/wcag_coverage.py) so
reviewers can confirm before acting.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Inputs whose recurrence across forms is a strong "this user is
# probably entering the same data twice" signal. We deliberately
# exclude generic names (`q`, `search`, `query`, `csrf_token`,
# anti-bot honeypots) where reuse is normal.
_BENIGN_NAME_PATTERNS = (
    "q", "query", "search", "csrf", "_token", "honeypot",
    "captcha", "submit", "_method",
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
    const forms = document.querySelectorAll('form');
    const result = [];
    forms.forEach((form, formIdx) => {
        const fields = [];
        for (const inp of form.querySelectorAll('input, select, textarea')) {
            const t = (inp.getAttribute('type') || 'text').toLowerCase();
            if (['hidden', 'submit', 'reset', 'button', 'image'].includes(t)) continue;
            fields.push({
                name: (inp.getAttribute('name') || '').trim(),
                id: inp.id || '',
                type: t,
                autocomplete: (inp.getAttribute('autocomplete') || '').trim(),
                selector: cssPath(inp),
            });
        }
        result.push({
            form_idx: formIdx,
            field_count: fields.length,
            fields,
        });
    });
    // Wizard signal: progressbar or stepped indicator anywhere?
    const progress = !!document.querySelector(
        '[role="progressbar"], [aria-current="step"], '
        + 'ol[class*="step"], ul[class*="step"], '
        + '[class*="wizard"], [class*="stepper"]'
    );
    return {forms: result, has_wizard_signal: progress};
}
"""


def _benign(name: str) -> bool:
    n = (name or "").lower()
    if not n:
        return True
    return any(pat in n for pat in _BENIGN_NAME_PATTERNS)


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    forms = probe.get("forms") or []

    # Collect (name → list of (form_idx, field_meta)).
    by_name: dict[str, list[dict[str, Any]]] = {}
    for f in forms:
        for field in f.get("fields") or []:
            name = (field.get("name") or "").strip()
            if not name or _benign(name):
                continue
            by_name.setdefault(name, []).append({**field, "form_idx": f.get("form_idx")})

    # Repeated names across forms with no autocomplete on either.
    seen_pairs: set[tuple[str, ...]] = set()
    for name, entries in by_name.items():
        forms_seen = {e.get("form_idx") for e in entries}
        if len(forms_seen) < 2:
            continue
        any_autocomplete = any((e.get("autocomplete") or "").strip() for e in entries)
        if any_autocomplete:
            continue
        # Use first occurrence as the issue's anchor element.
        anchor = entries[0]
        key = (name, anchor.get("selector", ""))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        issues.append(make_issue(
            issue_id=f"redundant-entry-no-autocomplete-{name}",
            module="redundant_entry",
            rule="redundant-entry-no-autocomplete",
            severity="moderate",
            wcag=["3.3.7"],
            confidence="medium",
            title=(
                f"Field name={name!r} appears on {len(forms_seen)} "
                "forms without an autocomplete token"
            ),
            description=(
                "WCAG 3.3.7 (Redundant Entry, level A, new in 2.2) "
                "requires that data the user already entered in the "
                "same process is auto-populated or available to "
                "select on later steps. This page has multiple forms "
                "asking for a field with the same name, and none "
                "declare an autocomplete token that browsers could "
                "use to remember the prior value. Heuristic — review "
                "and confirm that this name actually represents "
                "duplicate user input across steps (vs. e.g. two "
                "independent forms on a marketing page)."
            ),
            selector=anchor.get("selector", ""),
            details={
                "field_name": name,
                "form_count": len(forms_seen),
                "form_indices": sorted(forms_seen),
                "type": anchor.get("type", ""),
            },
            fix=(
                "Add `autocomplete=\"<purpose>\"` to each occurrence "
                "(see [audit/forms.py](audit/forms.py) for the "
                "supported tokens) so browsers can offer prior "
                "values. For server-driven multi-step flows, "
                "pre-fill the second occurrence from session state."
            ),
        ))

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("redundant_entry probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "form_count": len((probe or {}).get("forms") or []),
        "has_wizard_signal": (probe or {}).get("has_wizard_signal", False),
    }
