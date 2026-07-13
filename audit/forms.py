"""Forms module: labels, autocomplete, fieldsets, error associations.

Static analysis only. The plan's "submit empty and capture error text" flow
is deferred — it's interactive, page-specific, and easy to make flaky.
The rules below cover the problems that show up without needing to
interact with the form.

Rules implemented:
- forms-input-no-label               WCAG 3.3.2  critical   form control with no accessible label
- forms-radio-group-no-fieldset      WCAG 1.3.1  serious    grouped radios/checkboxes not wrapped in <fieldset><legend>
- forms-aria-invalid-no-description  WCAG 3.3.1  moderate   aria-invalid=true but no aria-describedby pointing to error text
- forms-missing-autocomplete         WCAG 1.3.5  minor      common personal-data field with no autocomplete attribute

`analyze(dom)` is pure-Python and testable without a browser.
"""

from __future__ import annotations

import time
from typing import Any

from audit._issue import make_issue

# Inputs that should carry autocomplete per WCAG 1.3.5 (AA).
# Keyed off input type OR name/id substring match.
AUTOCOMPLETE_TYPES = {"email", "tel", "password"}
AUTOCOMPLETE_NAME_SUBSTRINGS = (
    "email",
    "phone",
    "tel",
    "fname",
    "firstname",
    "lname",
    "lastname",
    "fullname",
    "address",
    "street",
    "city",
    "zip",
    "postal",
    "country",
)

# Recognised autocomplete tokens from WCAG 1.3.5 (which references the
# HTML Living Standard's autofill detail tokens). Keeping the list
# explicit lets us distinguish "author wrote a real token" from
# "author wrote `autocomplete=on` thinking that satisfied 1.3.5"
# (it doesn't — 1.3.5 requires a *purpose* token, not just on/off).
#
# Any token in this set is acceptable. Multi-token values (e.g.
# "shipping postal-code") are also valid; we tokenise on whitespace.
_VALID_AUTOCOMPLETE_TOKENS: frozenset[str] = frozenset({
    # Section / scope prefixes — valid as part of a multi-token value.
    "shipping", "billing", "home", "work", "mobile", "fax", "pager",
    # Identity
    "name", "honorific-prefix", "given-name", "additional-name",
    "family-name", "honorific-suffix", "nickname", "username",
    # Auth
    "new-password", "current-password", "one-time-code",
    # Org
    "organization-title", "organization",
    # Address
    "street-address", "address-line1", "address-line2", "address-line3",
    "address-level4", "address-level3", "address-level2", "address-level1",
    "country", "country-name", "postal-code",
    # Payment
    "cc-name", "cc-given-name", "cc-additional-name", "cc-family-name",
    "cc-number", "cc-exp", "cc-exp-month", "cc-exp-year", "cc-csc",
    "cc-type",
    "transaction-currency", "transaction-amount",
    # Other personal
    "language", "bday", "bday-day", "bday-month", "bday-year",
    "sex", "url", "photo",
    # Telephony
    "tel", "tel-country-code", "tel-national", "tel-area-code",
    "tel-local", "tel-extension",
    "email", "impp",
    # WebAuthn
    "webauthn",
})

# Values that authors set thinking they're 1.3.5-compliant but aren't.
# `on` and `off` switch browser autofill; they don't communicate
# *purpose* and so don't satisfy 1.3.5.
_NON_PURPOSE_AUTOCOMPLETE_TOKENS: frozenset[str] = frozenset({"on", "off"})

FORM_CONTROL_TAGS = {"input", "select", "textarea"}

# Input types that don't need a label (buttons expose their visible text).
UNLABELED_OK_TYPES = {"submit", "reset", "button", "hidden", "image"}


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
    function accessibleName(el) {
        const aria = el.getAttribute('aria-label');
        if (aria && aria.trim()) return aria.trim();
        const labelledby = el.getAttribute('aria-labelledby');
        if (labelledby) {
            const parts = labelledby.split(/\s+/).map(id => {
                const ref = document.getElementById(id);
                return ref ? (ref.textContent || '').trim() : '';
            });
            const joined = parts.filter(Boolean).join(' ');
            if (joined) return joined;
        }
        if (el.id) {
            const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (lab) return (lab.textContent || '').trim();
        }
        const wrappingLabel = el.closest('label');
        if (wrappingLabel) {
            const clone = wrappingLabel.cloneNode(true);
            clone.querySelectorAll('input,select,textarea').forEach(n => n.remove());
            const t = (clone.textContent || '').trim();
            if (t) return t;
        }
        const placeholder = el.getAttribute('placeholder');
        if (placeholder) return placeholder.trim();
        const title = el.getAttribute('title');
        if (title) return title.trim();
        return '';
    }

    const controls = [...document.querySelectorAll('input, select, textarea')].map(el => ({
        tag: el.tagName.toLowerCase(),
        type: (el.getAttribute('type') || '').toLowerCase(),
        name: el.getAttribute('name') || '',
        id: el.id || '',
        required: el.hasAttribute('required'),
        aria_required: el.getAttribute('aria-required') === 'true',
        aria_invalid: el.getAttribute('aria-invalid') === 'true',
        aria_describedby: el.getAttribute('aria-describedby') || '',
        autocomplete: el.getAttribute('autocomplete') || '',
        accessible_name: accessibleName(el),
        selector: cssPath(el),
        html: el.outerHTML.slice(0, 200)
    }));

    const ids = new Set();
    document.querySelectorAll('[id]').forEach(e => ids.add(e.id));

    // Build radio / checkbox groups keyed by (form, name).
    const groupMap = {};
    document.querySelectorAll(
        'input[type="radio"][name], input[type="checkbox"][name]'
    ).forEach(el => {
        const name = el.getAttribute('name');
        const formId = el.form ? (el.form.id || '__noform__') : '__global__';
        const key = formId + '::' + name + '::' + el.type;
        if (!groupMap[key]) {
            groupMap[key] = {
                type: el.type,
                name,
                members: [],
                in_fieldset: false,
                fieldset_has_legend: false,
                group_role: false
            };
        }
        const fs = el.closest('fieldset');
        if (fs) {
            groupMap[key].in_fieldset = true;
            if (fs.querySelector('legend')) {
                groupMap[key].fieldset_has_legend = true;
            }
        }
        const rg = el.closest('[role="radiogroup"], [role="group"]');
        if (rg) groupMap[key].group_role = true;
        groupMap[key].members.push({
            selector: cssPath(el),
            html: el.outerHTML.slice(0, 200)
        });
    });
    const groups = Object.values(groupMap).filter(g => g.members.length > 1);

    // id -> visible text lookup used by the error-content analyzer
    // (forms-error-not-descriptive). We only capture ids the
    // aria-describedby references could hit so the payload stays small.
    const id_to_text = {};
    for (const id of ids) {
        const el = document.getElementById(id);
        if (el) id_to_text[id] = (el.textContent || '').trim().slice(0, 200);
    }

    return { controls, groups, ids: [...ids], id_to_text };
}
"""


def _needs_autocomplete(control: dict[str, Any]) -> bool:
    t = (control.get("type") or "").lower()
    if t in AUTOCOMPLETE_TYPES:
        return True
    needle = f"{control.get('name','')} {control.get('id','')}".lower()
    return any(s in needle for s in AUTOCOMPLETE_NAME_SUBSTRINGS)


def analyze(dom: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    id_set: set[str] = set(dom.get("ids") or [])

    for idx, c in enumerate(dom.get("controls") or []):
        tag = (c.get("tag") or "").lower()
        ctype = (c.get("type") or "").lower()

        # Skip button-like inputs; their visible text is the name.
        if tag == "input" and ctype in UNLABELED_OK_TYPES:
            continue

        selector = c.get("selector", "")
        html_snippet = c.get("html", "")

        # 1. no accessible name
        if not (c.get("accessible_name") or "").strip():
            issues.append(
                make_issue(
                    issue_id=f"forms-input-no-label-{idx}",
                    module="forms",
                    rule="forms-input-no-label",
                    severity="critical",
                    wcag=["3.3.2", "4.1.2"],
                    title=f"<{tag}> has no accessible label",
                    description=(
                        "Screen-reader users hear only the field type (e.g. 'edit') "
                        "with no indication of what data to enter."
                    ),
                    selector=selector,
                    html_snippet=html_snippet,
                    details={"tag": tag, "type": ctype, "name": c.get("name", "")},
                    fix=(
                        "Add a <label for=\"{id}\">Description</label> (preferred) or "
                        'aria-label="Description" on the field.'
                    ).format(id=c.get("id", "...") or "..."),
                )
            )

        # 2. aria-invalid but no aria-describedby pointing to real element
        if c.get("aria_invalid"):
            refs = [r for r in (c.get("aria_describedby") or "").split() if r]
            resolved = [r for r in refs if r in id_set]
            if not resolved:
                issues.append(
                    make_issue(
                        issue_id=f"forms-aria-invalid-no-description-{idx}",
                        module="forms",
                        rule="forms-aria-invalid-no-description",
                        severity="moderate",
                        wcag=["3.3.1", "3.3.3"],
                        title="Field marked aria-invalid but has no associated error description",
                        description=(
                            "Screen readers announce the field as invalid but the user "
                            "never hears what went wrong."
                        ),
                        selector=selector,
                        html_snippet=html_snippet,
                        details={"aria_describedby": c.get("aria_describedby", "")},
                        fix=(
                            "Point aria-describedby at an element containing the error "
                            "text (e.g. a <span id='emailError'>Email is required</span>)."
                        ),
                    )
                )
            # 2b. Error text IS linked; check whether it actually helps.
            # The node text lookup is pre-populated by the extractor so
            # we have each referenced id's visible text. Heuristics:
            #   - fewer than 4 words OR
            #   - just the literal string "invalid" / "error" / "required"
            # → probably doesn't meet WCAG 3.3.3 Error Suggestion.
            id_to_text = (dom.get("id_to_text") or {})
            for ref in resolved:
                text = (id_to_text.get(ref) or "").strip()
                if not text:
                    continue
                word_count = len(text.split())
                is_generic = text.lower() in (
                    "invalid", "error", "required",
                    "invalid entry", "invalid input",
                    "bad value", "wrong",
                )
                if word_count < 4 or is_generic:
                    issues.append(
                        make_issue(
                            issue_id=f"forms-error-not-descriptive-{idx}-{ref}",
                            module="forms",
                            rule="forms-error-not-descriptive",
                            severity="moderate",
                            wcag=["3.3.3"],
                            confidence="medium",
                            title="Error message is too vague to be actionable",
                            description=(
                                f"The error text {text!r} is too short or "
                                "generic. WCAG 3.3.3 (AA) Error Suggestion "
                                "requires the message to tell users how to "
                                "fix the problem, not just that one exists."
                            ),
                            selector=selector,
                            html_snippet=html_snippet,
                            details={"error_text": text, "word_count": word_count},
                            fix=(
                                "Rewrite the error to describe the specific "
                                "problem and include a correction hint, e.g. "
                                "'Email must contain @' or 'Password must be "
                                "at least 10 characters.'"
                            ),
                        )
                    )

        # 3. missing autocomplete on common personal fields
        autocomplete_raw = (c.get("autocomplete") or "").strip()
        if _needs_autocomplete(c) and not autocomplete_raw:
            issues.append(
                make_issue(
                    issue_id=f"forms-missing-autocomplete-{idx}",
                    module="forms",
                    rule="forms-missing-autocomplete",
                    severity="minor",
                    wcag=["1.3.5"],
                    title=f"<{tag} type={ctype or 'text'}> appears to collect personal data without autocomplete",
                    description=(
                        "WCAG 1.3.5 (AA) asks that fields collecting information about "
                        "the user expose an appropriate autocomplete token so browsers "
                        "and assistive tech can help."
                    ),
                    selector=selector,
                    html_snippet=html_snippet,
                    details={"type": ctype, "name": c.get("name", "")},
                    fix='Add an autocomplete token, e.g. autocomplete="email" or "tel".',
                )
            )
        elif autocomplete_raw:
            # Validate the value when one is set. Two failure modes:
            #   (a) `on` / `off` — switches autofill but doesn't
            #       communicate purpose. 1.3.5 requires purpose.
            #   (b) unknown token — typo or made-up value. Browsers
            #       silently fall back to `on`, masking the bug.
            tokens = autocomplete_raw.lower().split()
            if any(t in _NON_PURPOSE_AUTOCOMPLETE_TOKENS for t in tokens):
                issues.append(
                    make_issue(
                        issue_id=f"forms-autocomplete-on-off-{idx}",
                        module="forms",
                        rule="forms-autocomplete-on-off",
                        severity="moderate",
                        wcag=["1.3.5"],
                        confidence="high",
                        title=(
                            f"autocomplete={autocomplete_raw!r} does not "
                            "satisfy WCAG 1.3.5"
                        ),
                        description=(
                            "WCAG 1.3.5 (AA, Identify Input Purpose) "
                            "requires fields collecting personal data "
                            "to declare *what* purpose they serve "
                            "(e.g. \"email\", \"given-name\"). The "
                            "tokens `on` and `off` only toggle the "
                            "browser's autofill behaviour — they do "
                            "not name a purpose, so assistive tools "
                            "cannot help users reuse remembered "
                            "values for this field."
                        ),
                        selector=selector,
                        html_snippet=html_snippet,
                        details={"value": autocomplete_raw, "type": ctype},
                        fix=(
                            "Replace with a purpose token, e.g. "
                            "autocomplete=\"email\" or "
                            "autocomplete=\"new-password\"."
                        ),
                    )
                )
            else:
                bad = [t for t in tokens if t not in _VALID_AUTOCOMPLETE_TOKENS]
                if bad:
                    issues.append(
                        make_issue(
                            issue_id=f"forms-autocomplete-unknown-token-{idx}",
                            module="forms",
                            rule="forms-autocomplete-unknown-token",
                            severity="minor",
                            wcag=["1.3.5"],
                            confidence="high",
                            title=(
                                f"autocomplete contains unrecognised "
                                f"token(s): {', '.join(bad)}"
                            ),
                            description=(
                                "Browsers silently fall back to default "
                                "behaviour when they don't recognise a "
                                "token, so a typo here looks like the "
                                "field is autofill-aware while in fact "
                                "it isn't. WCAG 1.3.5 only counts the "
                                "field as identified when the token is "
                                "drawn from the recognised set."
                            ),
                            selector=selector,
                            html_snippet=html_snippet,
                            details={
                                "value": autocomplete_raw,
                                "unknown_tokens": bad,
                            },
                            fix=(
                                "Use a recognised autofill token from "
                                "the HTML living standard (e.g. "
                                "given-name, family-name, email, tel, "
                                "address-line1, postal-code, cc-number). "
                                "If none fits, omit autocomplete entirely."
                            ),
                        )
                    )

    # 4. radio / checkbox groups without fieldset+legend or labelled group role
    for idx, g in enumerate(dom.get("groups") or []):
        has_grouping = (g.get("in_fieldset") and g.get("fieldset_has_legend")) or g.get(
            "group_role"
        )
        if has_grouping:
            continue
        first = (g.get("members") or [{}])[0]
        gtype = g.get("type", "radio")
        issues.append(
            make_issue(
                issue_id=f"forms-radio-group-no-fieldset-{idx}",
                module="forms",
                rule="forms-radio-group-no-fieldset",
                severity="serious",
                wcag=["1.3.1"],
                title=f"{gtype} group '{g.get('name','')}' has no <fieldset><legend>",
                description=(
                    "Without a fieldset+legend (or role='radiogroup' with an "
                    "accessible name), screen-reader users cannot tell that the "
                    "individual options belong to the same question."
                ),
                selector=first.get("selector", ""),
                html_snippet=first.get("html", ""),
                details={
                    "name": g.get("name", ""),
                    "type": gtype,
                    "option_count": len(g.get("members") or []),
                },
                fix=(
                    "Wrap the options in <fieldset><legend>Question text</legend>...</fieldset>."
                ),
            )
        )

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    start = time.time()
    try:
        dom = page.evaluate(_EXTRACT_JS)
    except Exception as exc:
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(dom)

    # WCAG 3.3.4 Error Prevention (Legal, Financial, Data) — level AA.
    # Only applies when the caller declared the page's consequence
    # class. We look for signals that review / confirm / undo exist:
    # a second submit step, a dialog with a title containing "confirm",
    # or a submit button whose label mentions review/confirm. Very
    # coarse — always emit at confidence=low and document in the fix.
    consequence = (options.get("form_consequence") or "general").lower()
    if consequence in ("legal", "financial", "data"):
        try:
            has_review = bool(page.evaluate(
                r"""() => {
                    const forms = document.querySelectorAll('form');
                    if (!forms.length) return true;  // nothing to check
                    // Confirm dialog visible on page?
                    const dialog = document.querySelector(
                        '[role="dialog"], [role="alertdialog"]'
                    );
                    if (dialog && /confirm|review|are you sure/i.test(dialog.textContent || '')) {
                        return true;
                    }
                    // Any submit button whose label implies review?
                    const submits = document.querySelectorAll(
                        'button[type="submit"], input[type="submit"]'
                    );
                    for (const b of submits) {
                        const t = (b.textContent || b.value || '').trim();
                        if (/review|confirm|verify|preview/i.test(t)) return true;
                    }
                    // No clear review step — likely missing.
                    return false;
                }"""
            ))
        except Exception:
            has_review = True  # fail-open to avoid spurious fires
        if not has_review:
            issues.append(
                make_issue(
                    issue_id="forms-no-review-step",
                    module="forms",
                    rule="forms-no-review-step",
                    severity="serious",
                    wcag=["3.3.4"],
                    confidence="low",
                    title=(
                        f"No review / confirm / undo step detected "
                        f"for a {consequence}-consequence form"
                    ),
                    description=(
                        f"This page is declared to submit {consequence} "
                        "data. WCAG 3.3.4 (AA) requires a mechanism to "
                        "review + confirm (or undo) such submissions. "
                        "We did not detect a confirm dialog, review step, "
                        "or review-labelled submit button."
                    ),
                    fix=(
                        "Add either (a) a confirmation dialog listing "
                        "the data being submitted with an explicit "
                        "Confirm button, (b) a 'Review order' step "
                        "between editing and submitting, or (c) an "
                        "'Undo' affordance that reverses the submission "
                        "within a reasonable window."
                    ),
                )
            )

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "controls": len(dom.get("controls") or []),
        "groups": len(dom.get("groups") or []),
    }
