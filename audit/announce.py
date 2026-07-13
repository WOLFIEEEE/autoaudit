"""Announcement preview — what a screen reader will read for each element.

This is the Tier-1 "what gets announced" view: the computed accessible
NAME, ROLE, and STATES that a screen reader reads from, pulled straight
from Chromium's accessibility tree via the Chrome DevTools Protocol
(`Accessibility.getFullAXTree`). It is:

  - **deterministic** — same page, same output, every run;
  - **headless & fast** — no screen reader, no focus stealing, runs
    anywhere the audit runs;
  - **authoritative for name/role/state** — it is the exact tree NVDA,
    JAWS, and VoiceOver build their speech from.

`format_announcement()` assembles those fields into an approximate
spoken phrase (e.g. "Search, button, collapsed"). That string is a
**close approximation, not verbatim NVDA speech** — a real screen reader
layers on its own verbosity (position-in-set, "clickable", landmark
boundaries, punctuation echo) that only Path B (the NVDA worker) can
reproduce. We label it as approximate everywhere it surfaces.

Pure functions (`_node_to_record`, `format_announcement`) take plain
dicts, so unit tests need neither a browser nor CDP.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit.browser import open_page

log = logging.getLogger(__name__)

# Default cap on returned elements. A large page has thousands of AX
# nodes; a preview past a few hundred is unreadable and slow to resolve
# selectors for.
DEFAULT_LIMIT = 300

# Roles that always carry an announcement worth previewing.
_INTERACTIVE_ROLES = {
    "link", "button", "textbox", "checkbox", "radio", "combobox",
    "listbox", "menuitem", "menuitemcheckbox", "menuitemradio", "tab",
    "switch", "slider", "spinbutton", "searchbox", "option", "treeitem",
    "gridcell", "menuitemradio",
}
# Interactive + structural roles included in the preview by default.
_DEFAULT_ROLES = _INTERACTIVE_ROLES | {"heading", "image", "img", "dialog"}

# AX properties we surface as element states.
_STATE_PROPS = {
    "focusable", "focused", "disabled", "required", "expanded", "checked",
    "pressed", "selected", "readonly", "invalid", "modal", "multiselectable",
}

# Roles for which "not checked" / "not selected" is meaningful to speak.
_CHECKABLE_ROLES = {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"}
_VALUE_ROLES = {"textbox", "combobox", "slider", "spinbutton", "searchbox"}


def _node_to_record(node: dict[str, Any]) -> dict[str, Any]:
    """Transform one AX-tree node into a flat announcement record.

    Pure — takes the raw CDP node dict, returns role / name / description
    / value / heading level / states / backend node id.
    """
    role = (node.get("role") or {}).get("value")
    name = ((node.get("name") or {}).get("value") or "")
    description = ((node.get("description") or {}).get("value") or "")
    value = ((node.get("value") or {}).get("value") or "")
    level: int | None = None
    states: dict[str, Any] = {}
    for prop in node.get("properties") or []:
        pname = prop.get("name")
        pval = (prop.get("value") or {}).get("value")
        if pname == "level":
            level = pval
        elif pname in _STATE_PROPS:
            states[pname] = pval
    return {
        "role": role,
        "name": name if isinstance(name, str) else str(name),
        "description": description,
        "value": value if isinstance(value, str) else str(value),
        "level": level,
        "states": states,
        "backend_id": node.get("backendDOMNodeId"),
        "ignored": bool(node.get("ignored")),
    }


def format_announcement(record: dict[str, Any]) -> str:
    """Assemble an APPROXIMATE screen-reader phrase from a record.

    Ordered name-then-role (screen-reader focus-mode convention) with the
    speakable states appended. This is a deterministic approximation, not
    verbatim NVDA output.
    """
    role = record.get("role") or "group"
    name = (record.get("name") or "").strip()
    parts: list[str] = [name if name else "(no accessible name)"]

    if role == "heading" and record.get("level"):
        parts.append(f"heading level {record['level']}")
    else:
        parts.append(role)

    st = record.get("states") or {}
    if st.get("expanded") is True:
        parts.append("expanded")
    elif st.get("expanded") is False:
        parts.append("collapsed")
    if st.get("checked") is True:
        parts.append("checked")
    elif st.get("checked") is False and role in _CHECKABLE_ROLES:
        parts.append("not checked")
    if st.get("selected"):
        parts.append("selected")
    if st.get("disabled"):
        parts.append("unavailable")
    if st.get("required"):
        parts.append("required")

    value = (record.get("value") or "").strip()
    if value and role in _VALUE_ROLES:
        parts.append(f"value {value}")

    return ", ".join(parts)


# Returns the CSS path of the resolved element (`this`).
_CSS_PATH_FN = r"""
function () {
    function p(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            let s = cur.tagName.toLowerCase();
            const par = cur.parentElement;
            if (par) {
                const sib = [...par.children].filter(c => c.tagName === cur.tagName);
                if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(cur) + 1) + ')';
            }
            parts.unshift(s);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    return p(this);
}
"""


def _selector_for_backend(client, backend_id: int | None) -> str:
    """Resolve an AX node's backend DOM id to a CSS selector via CDP."""
    if backend_id is None:
        return ""
    try:
        resolved = client.send("DOM.resolveNode", {"backendNodeId": backend_id})
        object_id = (resolved.get("object") or {}).get("objectId")
        if not object_id:
            return ""
        res = client.send("Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": _CSS_PATH_FN,
            "returnByValue": True,
        })
        return (res.get("result") or {}).get("value") or ""
    except Exception:
        return ""


def announce_tree(
    page,
    *,
    roles: set[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return per-element announcement records for the page's a11y tree.

    Each record: {selector, role, name, description, value, level,
    states, announcement}. `announcement` is the approximate spoken
    phrase; the discrete fields are authoritative.
    """
    roles = roles if roles is not None else _DEFAULT_ROLES
    client = page.context.new_cdp_session(page)
    try:
        client.send("Accessibility.enable")
        client.send("DOM.enable")
        client.send("DOM.getDocument", {"depth": -1})
        tree = client.send("Accessibility.getFullAXTree")
    except Exception as exc:
        log.warning("announce: getFullAXTree failed: %s", exc)
        return []

    records: list[dict[str, Any]] = []
    for node in tree.get("nodes", []):
        if node.get("ignored"):
            continue
        rec = _node_to_record(node)
        if roles and rec["role"] not in roles:
            continue
        # An unnamed structural node (e.g. an empty heading) is worth
        # showing; an unnamed non-interactive-and-non-structural node is
        # noise. Interactive roles are always shown (an unnamed control is
        # exactly what a reviewer wants to spot).
        if not rec["name"] and rec["role"] not in (_INTERACTIVE_ROLES | {"heading"}):
            continue
        records.append(rec)
        if len(records) >= limit:
            break

    for rec in records:
        rec["selector"] = _selector_for_backend(client, rec.pop("backend_id"))
        rec.pop("ignored", None)
        rec["announcement"] = format_announcement(rec)
    return records


def run_announcement_preview(
    url: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Open `url` and return the Tier-1 announcement preview for it."""
    options = dict(options or {})
    start = time.time()
    limit = int(options.get("limit") or DEFAULT_LIMIT)
    with open_page(url, options, headless=True) as page:
        elements = announce_tree(page, limit=limit)
    return {
        "url": url,
        "mode": "announce",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - start, 2),
        "element_count": len(elements),
        "elements": elements,
        "note": (
            "Tier-1 accessibility-tree preview: computed name / role / "
            "state as Chromium exposes them to assistive tech. The "
            "'announcement' field is an APPROXIMATION of screen-reader "
            "output, not verbatim NVDA speech — run the NVDA worker "
            "(Path B) for exact spoken output."
        ),
    }
