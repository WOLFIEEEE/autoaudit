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

    return { controls, groups, ids: [...ids] };
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
                    principle="understandable",
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
                        principle="understandable",
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

        # 3. missing autocomplete on common personal fields
        if _needs_autocomplete(c) and not (c.get("autocomplete") or "").strip():
            issues.append(
                make_issue(
                    issue_id=f"forms-missing-autocomplete-{idx}",
                    module="forms",
                    rule="forms-missing-autocomplete",
                    severity="minor",
                    principle="understandable",
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
                principle="perceivable",
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


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
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
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "controls": len(dom.get("controls") or []),
        "groups": len(dom.get("groups") or []),
    }
