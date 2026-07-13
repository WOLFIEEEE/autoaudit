"""Fake button / silent clickable — WCAG 2.1.1 (A) + 4.1.2 (A).

The single most common real-world keyboard failure: an element that
*looks and behaves* like a control (a styled `<div class="btn">`, a
`<span>` with a click handler) but is built from a non-interactive tag.
Because it has no `role` and no `tabindex`, it is:

  - invisible to a keyboard tab walk (it's never focusable, so a
    walk-based detector never reaches it), and
  - exposed to assistive tech as a generic container, not a control,
    so a "silent interactive" / a11y-tree check never classifies it as
    interactive, and
  - ignored by axe-core, which deliberately won't infer "button" from a
    class name or a JS `addEventListener` handler it can't see.

That blind spot is exactly why these slip through every other module.
This rule closes it with a positive heuristic over the rendered DOM.

A closely-related failure is an `<a>` used as a control but with **no
`href`**: an anchor without an href has no implicit link role, is not a
real link, and pressing Enter does not activate it — yet authors wire a
click handler to it and style it like a button. We flag that too
(`fake-button-anchor-no-href`), since it is the same class of bug seen
from the anchor side.

We flag an element when ALL of the following hold:

  1. It's a non-interactive tag (`div`, `span`, `p`, `i`, `b`, `em`,
     `strong`), OR an `<a>` with no `href` attribute — native controls
     and real `<a href>` links are already keyboard operable and out of
     scope.
  2. Its computed `cursor` is `pointer` — the page is telling the user
     "this is clickable". This is the precision anchor: it excludes
     layout containers (e.g. a `class="btn-row"` wrapper) that merely
     match a button-ish class but aren't themselves clickable.
  3. It is NOT keyboard-focusable (`tabIndex < 0`, no `tabindex`) — a
     focusable div with a key handler is a different, lesser concern.
  4. It declares NO interactive ARIA role (a `role="button"` div is a
     separate problem handled by the ARIA/widgets modules).
  5. It contains NO focusable descendant — a "clickable card" whose real
     link/button lives inside is keyboard-reachable via that child, so
     flagging the wrapper would be a false positive.
  6. It has NO interactive ancestor — text spans inside a real `<a>` or
     `<button>` inherit `cursor:pointer` from it but are not themselves
     controls; the ancestor is already keyboard operable.
  7. It has a short text label OR a button-ish class — a sanity gate so
     we don't flag a pointer-cursored block of prose.

Confidence is `medium`: `cursor:pointer` + non-focusable + role-less is
a strong signal, but author intent can't be proven from markup alone, so
a reviewer should confirm a real click handler is attached.

Maps to 2.1.1 (the control can't be operated by keyboard) and 4.1.2 (it
exposes no role/name/state to assistive tech).

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

# Tags we treat as non-interactive carriers of a fake control. Native
# interactive elements (button, a[href], input, select, textarea,
# summary) are excluded — they're keyboard operable by default.
_NONINTERACTIVE_TAGS = {"div", "span", "p", "i", "b", "em", "strong", "section"}

# ARIA roles that DO expose an interactive control. An element declaring
# one of these is out of scope here (it has a role; whether it's also
# focusable is the ARIA/widgets module's job).
_INTERACTIVE_ROLES = {
    "button", "link", "checkbox", "radio", "menuitem", "menuitemcheckbox",
    "menuitemradio", "tab", "switch", "option", "combobox", "slider",
    "spinbutton", "textbox", "searchbox", "treeitem", "gridcell",
}

_BTN_CLASS = re.compile(r"(?:^|[\s_-])(?:btn|button)(?:[\s_-]|$)", re.IGNORECASE)


def _looks_like_button_class(classes: str) -> bool:
    return bool(_BTN_CLASS.search(classes or ""))


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure analysis over the probe snapshot. No browser required."""
    issues: list[dict[str, Any]] = []

    for idx, item in enumerate(probe.get("candidates") or []):
        tag = (item.get("tag") or "").lower()
        # An <a> with no href is not a real link — treat it as a
        # non-interactive carrier of a fake control.
        is_anchor_no_href = tag == "a" and not item.get("has_href")
        if tag not in _NONINTERACTIVE_TAGS and not is_anchor_no_href:
            continue
        # Must visually present as clickable.
        if (item.get("cursor") or "") != "pointer":
            continue
        # A focusable div/span is a lesser, different concern — skip it.
        # An href-less anchor is flagged regardless of focusability,
        # because the missing href (no link role, Enter won't activate)
        # is itself the defect.
        if not is_anchor_no_href and item.get("focusable"):
            continue
        role = (item.get("role") or "").strip().lower()
        if role in _INTERACTIVE_ROLES:
            continue
        if not item.get("visible"):
            continue
        # A real, keyboard-operable control inside (clickable-card
        # pattern) means the keyboard path exists — don't flag the
        # wrapper.
        if item.get("has_focusable_descendant"):
            continue
        # Inside a real <a>/<button> (e.g. styled text spans in a logo
        # link) — the ancestor is the control and is already operable.
        if item.get("has_interactive_ancestor"):
            continue
        text = (item.get("text") or "").strip()
        btn_class = _looks_like_button_class(item.get("classes") or "")
        # Sanity gate: a label OR a button-ish class. Avoids flagging a
        # pointer-cursored block of prose with no control affordance.
        if not text and not btn_class:
            continue

        label = text[:60] if text else "(no text)"
        if is_anchor_no_href:
            title = f"Clickable <a> {label!r} has no href — not a real link"
            description = (
                "This <a> shows a pointer cursor"
                + (f" and a button-style class ({item.get('classes','')!r})"
                   if btn_class else "")
                + " and is used as a control, but it has no href. An "
                "anchor without an href has no implicit link role, is not "
                "announced as a link, and does not activate on Enter — so "
                "keyboard users cannot operate it even when it takes "
                "focus. WCAG 2.1.1 (A) requires keyboard operability and "
                "4.1.2 (A) requires a programmatic role. Use a <button> "
                "for an action, or give the anchor a real href for "
                "navigation. Heuristic — confirm a click handler is "
                "attached (addEventListener handlers are invisible to a "
                "static scan)."
            )
            fix = (
                "Use a native <button type=\"button\"> for an action, or "
                "add a real href if it navigates. Avoid an href-less <a> "
                "wired to click only."
            )
        else:
            title = f"Clickable <{tag}> {label!r} is not a real button"
            description = (
                f"This <{tag}> shows a pointer cursor"
                + (f" and a button-style class ({item.get('classes','')!r})"
                   if btn_class else "")
                + ", so it presents as a control, but it is not a native "
                "button/link, declares no interactive ARIA role, and is "
                "not keyboard-focusable (no tabindex). Keyboard and "
                "switch users cannot reach or activate it, and assistive "
                "tech announces it as plain text, not a control. WCAG "
                "2.1.1 (A) requires keyboard operability and 4.1.2 (A) "
                "requires a programmatic role. Heuristic — confirm a "
                "click handler is attached (handlers added via "
                "addEventListener are not visible to a static scan)."
            )
            fix = (
                "Use a native <button> (or <a href> for navigation). If "
                "the tag must stay, add role=\"button\", tabindex=\"0\", "
                "and keydown handling for Enter and Space so the control "
                "is keyboard operable and exposed to assistive tech."
            )
        # Shared issue fields; only the literal rule id differs per
        # branch. Passing rule= as a literal (not a variable) keeps the
        # rule discoverable by the AST orphan-check in
        # tests/test_rule_versions.py.
        common = dict(
            issue_id=f"fake-button-{idx}",
            module="fake_button",
            severity="serious",
            wcag=["2.1.1", "4.1.2"],
            confidence="medium",
            title=title,
            description=description,
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            details={
                "tag": tag,
                "classes": item.get("classes", ""),
                "cursor": item.get("cursor", ""),
                "text": text,
                "has_role": bool(role),
                "has_href": bool(item.get("has_href")),
            },
            fix=fix,
        )
        if is_anchor_no_href:
            issues.append(make_issue(rule="fake-button-anchor-no-href", **common))
        else:
            issues.append(make_issue(rule="fake-button-noninteractive", **common))

    return issues


# Probe: collect candidate non-interactive elements that show a pointer
# cursor (the bounded pre-filter), with the fields analyze() needs.
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

    const FOCUSABLE_SEL = 'a[href],button,input,select,textarea,summary,'
        + '[tabindex]:not([tabindex^="-"]),[role=button],[role=link],'
        + '[contenteditable=""],[contenteditable=true]';
    // Include <a> so we can catch href-less anchors used as controls;
    // real <a href> links are filtered out below.
    const TAGS = ['div','span','p','i','b','em','strong','section','a'];

    const candidates = [];
    const els = document.querySelectorAll(TAGS.join(','));
    let scanned = 0;
    for (const el of els) {
        if (scanned >= 4000) break;
        scanned += 1;
        const tag = el.tagName.toLowerCase();
        // A real link (anchor WITH href) is keyboard operable — skip.
        if (tag === 'a' && el.hasAttribute('href')) continue;
        const cs = getComputedStyle(el);
        // Bounded pre-filter: only pointer-cursor elements are possible
        // fake controls. Keeps the payload small on large pages.
        if (cs.cursor !== 'pointer') continue;
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        if (parseFloat(cs.opacity) === 0) continue;
        const r = el.getBoundingClientRect();
        const visible = r.width >= 4 && r.height >= 4;
        if (!visible) continue;

        candidates.push({
            tag: tag,
            role: el.getAttribute('role'),
            has_href: el.hasAttribute('href'),
            cursor: cs.cursor,
            focusable: el.tabIndex >= 0,
            classes: (el.className && el.className.toString
                ? el.className.toString() : '').slice(0, 120),
            text: (el.textContent || '').trim().slice(0, 80),
            visible: true,
            has_focusable_descendant: !!el.querySelector(FOCUSABLE_SEL),
            // closest() from the parent so we detect an interactive
            // ANCESTOR (a real <a>/<button> the element sits inside),
            // not the element itself.
            has_interactive_ancestor: !!(el.parentElement && el.parentElement.closest(
                'a[href],button,summary,label,[role=button],[role=link],'
                + '[role=menuitem],[role=tab],[role=option],[contenteditable=""],'
                + '[contenteditable=true]'
            )),
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
        });
        if (candidates.length >= 80) break;
    }
    return {candidates};
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("fake_button probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "candidate_count": len((probe or {}).get("candidates") or []),
    }
