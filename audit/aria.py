"""ARIA module: role validity and label/description reference integrity.

Rules implemented:
- aria-invalid-role         WCAG 4.1.2  serious   role is not a valid ARIA role
- aria-labelledby-missing   WCAG 4.1.2  serious   aria-labelledby points to a missing id
- aria-describedby-missing  WCAG 4.1.2  moderate  aria-describedby points to a missing id
- aria-hidden-focusable     WCAG 4.1.2  serious   aria-hidden="true" on a focusable element

Scope note: ARIA validation is deliberately narrow here. axe-core covers a
larger matrix of role/property compatibility; these rules focus on
reference integrity that axe historically leaves to their experimental ruleset.
"""

from __future__ import annotations

import time
from typing import Any

from audit._issue import make_issue

# ARIA 1.2 roles that are valid in HTML content.
VALID_ROLES = frozenset(
    {
        "alert", "alertdialog", "application", "article", "banner", "blockquote",
        "button", "caption", "cell", "checkbox", "code", "columnheader", "combobox",
        "complementary", "contentinfo", "definition", "deletion", "dialog",
        "directory", "document", "emphasis", "feed", "figure", "form",
        "generic", "graphics-document", "graphics-object", "graphics-symbol",
        "grid", "gridcell", "group", "heading", "img", "insertion",
        "link", "list", "listbox", "listitem", "log", "main", "marquee",
        "math", "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio",
        "meter", "navigation", "none", "note", "option", "paragraph", "presentation",
        "progressbar", "radio", "radiogroup", "region", "row", "rowgroup",
        "rowheader", "scrollbar", "search", "searchbox", "separator", "slider",
        "spinbutton", "status", "strong", "subscript", "superscript", "switch",
        "tab", "table", "tablist", "tabpanel", "term", "textbox", "time",
        "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem",
    }
)


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
    function isFocusable(el) {
        if (el.tabIndex < 0) return false;
        const tag = el.tagName.toLowerCase();
        if (['a','button','input','select','textarea','iframe'].includes(tag)) {
            if (tag === 'a' && !el.hasAttribute('href')) return false;
            return !el.disabled;
        }
        return el.hasAttribute('tabindex') && el.tabIndex >= 0;
    }

    const ids = new Set();
    document.querySelectorAll('[id]').forEach(e => ids.add(e.id));

    const withRole = [...document.querySelectorAll('[role]')].map(el => ({
        role: (el.getAttribute('role') || '').trim(),
        selector: cssPath(el),
        html: el.outerHTML.slice(0, 200)
    }));
    const labelledby = [...document.querySelectorAll('[aria-labelledby]')].map(el => ({
        refs: (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean),
        selector: cssPath(el),
        html: el.outerHTML.slice(0, 200)
    }));
    const describedby = [...document.querySelectorAll('[aria-describedby]')].map(el => ({
        refs: (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean),
        selector: cssPath(el),
        html: el.outerHTML.slice(0, 200)
    }));
    const hiddenFocusable = [...document.querySelectorAll('[aria-hidden="true"]')]
        .filter(el => isFocusable(el) ||
            el.querySelector('a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])'))
        .map(el => ({
            selector: cssPath(el),
            html: el.outerHTML.slice(0, 200),
            focusable_child: !isFocusable(el)
        }));

    return {
        ids: [...ids],
        roles: withRole,
        labelledby,
        describedby,
        hidden_focusable: hiddenFocusable
    };
}
"""


def analyze(dom: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    id_set: set[str] = set(dom.get("ids") or [])

    # Invalid role
    for idx, r in enumerate(dom.get("roles") or []):
        role = (r.get("role") or "").lower()
        # Multiple roles are allowed; the first valid one applies. Flag only if
        # none of the space-separated tokens is valid.
        tokens = [t for t in role.split() if t]
        if tokens and not any(t in VALID_ROLES for t in tokens):
            issues.append(
                make_issue(
                    issue_id=f"aria-invalid-role-{idx}",
                    module="aria",
                    rule="aria-invalid-role",
                    severity="serious",
                    wcag=["4.1.2"],
                    title=f'Unknown ARIA role "{role}"',
                    description=(
                        "Assistive tech ignores unknown roles; the element's semantics "
                        "fall back to its native tag, which may not match intent."
                    ),
                    selector=r.get("selector", ""),
                    html_snippet=r.get("html", ""),
                    details={"role": role},
                    fix="Use a valid ARIA 1.2 role, or remove the attribute to rely on native semantics.",
                )
            )

    # labelledby / describedby ref integrity
    for idx, lb in enumerate(dom.get("labelledby") or []):
        missing = [ref for ref in lb.get("refs", []) if ref not in id_set]
        if missing:
            issues.append(
                make_issue(
                    issue_id=f"aria-labelledby-missing-{idx}",
                    module="aria",
                    rule="aria-labelledby-missing",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="aria-labelledby references an ID that doesn't exist",
                    description=(
                        "If the referenced element is missing, the control has no "
                        "accessible name."
                    ),
                    selector=lb.get("selector", ""),
                    html_snippet=lb.get("html", ""),
                    details={"missing_ids": missing},
                    fix="Ensure each ID in aria-labelledby matches an existing element's id.",
                )
            )

    for idx, db in enumerate(dom.get("describedby") or []):
        missing = [ref for ref in db.get("refs", []) if ref not in id_set]
        if missing:
            issues.append(
                make_issue(
                    issue_id=f"aria-describedby-missing-{idx}",
                    module="aria",
                    rule="aria-describedby-missing",
                    severity="moderate",
                    wcag=["4.1.2"],
                    title="aria-describedby references an ID that doesn't exist",
                    description="The accessible description will be empty.",
                    selector=db.get("selector", ""),
                    html_snippet=db.get("html", ""),
                    details={"missing_ids": missing},
                    fix="Ensure each ID in aria-describedby matches an existing element's id.",
                )
            )

    # aria-hidden on focusable
    for idx, h in enumerate(dom.get("hidden_focusable") or []):
        issues.append(
            make_issue(
                issue_id=f"aria-hidden-focusable-{idx}",
                module="aria",
                rule="aria-hidden-focusable",
                severity="serious",
                wcag=["4.1.2"],
                title='aria-hidden="true" applied to a focusable element',
                description=(
                    "Hiding a focusable element from assistive tech while leaving it "
                    "in the tab order traps keyboard users on an invisible control."
                ),
                selector=h.get("selector", ""),
                html_snippet=h.get("html", ""),
                details={"focusable_child": h.get("focusable_child", False)},
                fix="Remove aria-hidden, or remove the element from the tab order (tabindex=-1).",
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
        "roles_seen": len(dom.get("roles") or []),
        "labelledby_refs": len(dom.get("labelledby") or []),
    }
