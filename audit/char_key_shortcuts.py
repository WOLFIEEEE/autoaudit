"""Character Key Shortcuts — WCAG 2.1.4 (A).

WCAG 2.1.4 says: if a keyboard shortcut is implemented using *only*
letter, punctuation, number, or symbol characters, then at least one of
the following must be true — the shortcut can be turned off, it can be
remapped to use a modifier key, or it's only active when the relevant
component has focus.

We cannot inventory every shortcut a page's JavaScript implements (the
handler logic is arbitrary), and we deliberately do not claim to. But
two failure patterns are detectable from the DOM with high precision,
and both are common real-world 2.1.4 violations:

  1. **Single-character `accesskey`.** An `accesskey="s"` attribute
     binds a character key (via the browser's modifier convention) to a
     control. These are global, can't be turned off by the user, and
     collide with screen-reader / speech-input pass-through keys. A
     single-character accesskey is the textbook 2.1.4 concern. (Multi-
     character accesskey lists are rare and we report each binding.)

  2. **Unguarded single-key document handlers.** An inline
     `onkeydown` / `onkeypress` handler on `document` / `body` /
     `window`, or a `[data-keyboard-shortcut]`-style attribute, whose
     source compares `event.key` / `event.keyCode` against a single
     character WITHOUT checking a modifier (`ctrlKey` / `altKey` /
     `metaKey`). These fire no matter where focus is — exactly what
     2.1.4 guards against. We only inspect handler source we can read
     from the DOM (inline attributes); listeners attached via
     `addEventListener` are invisible to us and are NOT claimed.

Everything here is heuristic and reported at `confidence: medium` (the
accesskey rule) or `low` (the handler-source rule) — the registry keeps
2.1.4 at the `partial` tier because whether a turn-off / remap / focus-
scope mechanism exists still needs human verification.

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

# Single-character accesskey is the unambiguous case. We treat any
# accesskey token of length 1 as a character-key shortcut.
_MODIFIER_TOKENS = ("ctrlkey", "metakey", "altkey", "event.ctrlkey",
                    "event.metakey", "event.altkey", "e.ctrlkey",
                    "e.metakey", "e.altkey")

# Source patterns that indicate the handler keys off a single character.
# We look for `key === 'x'`, `key == "x"`, `keyCode === 83`, `which === 83`,
# and `charCode`. Kept intentionally narrow to avoid false positives on
# handlers that merely read the key for logging.
_SINGLE_KEY_SRC = re.compile(
    r"""
    (?:\.|\b)(?:key|code)\s*===?\s*['"][^'"]['"]   # key === 'x'  (1 char)
    | (?:\.|\b)(?:keyCode|which|charCode)\s*===?\s*\d{1,3}\b  # keyCode === 83
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _handler_has_modifier_guard(src: str) -> bool:
    """True when the handler source checks a modifier key — meaning the
    shortcut is NOT a bare character key and 2.1.4 doesn't apply."""
    low = src.lower()
    return any(tok in low for tok in _MODIFIER_TOKENS)


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure analysis over the probe snapshot. No browser required."""
    issues: list[dict[str, Any]] = []

    # ---- Pattern 1: single-character accesskey ----------------------
    for idx, item in enumerate(probe.get("accesskeys") or []):
        key = (item.get("accesskey") or "").strip()
        # accesskey can be a space-separated list of candidates; a
        # *single character* candidate is the 2.1.4 concern. Report when
        # any candidate is one character long.
        candidates = [k for k in key.split() if k]
        single = [k for k in candidates if len(k) == 1]
        if not single:
            continue
        issues.append(make_issue(
            issue_id=f"char-key-accesskey-{idx}",
            module="char_key_shortcuts",
            rule="char-key-shortcut-accesskey",
            severity="moderate",
            wcag=["2.1.4"],
            confidence="medium",
            title="Single-character access key may violate Character Key Shortcuts",
            description=(
                f"This element declares accesskey={key!r}, binding the "
                f"character key {single[0]!r} as a global shortcut. "
                "Single-character shortcuts fire regardless of focus and "
                "cannot be turned off by the user, which collides with "
                "screen-reader and speech-input pass-through keys. WCAG "
                "2.1.4 (A) requires that such a shortcut can be turned "
                "off, remapped to include a modifier key, or be active "
                "only when its component has focus. Verify one of those "
                "mechanisms exists."
            ),
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            details={
                "accesskey": key,
                "single_char_candidates": single,
                "tag": item.get("tag", ""),
            },
            fix=(
                "Provide a user setting to turn the access key off or "
                "remap it, require a modifier (Ctrl/Alt/Cmd) in addition "
                "to the character, or scope the shortcut to when the "
                "control is focused. Removing the accesskey is also "
                "acceptable when the shortcut isn't essential."
            ),
        ))

    # ---- Pattern 2: unguarded single-key inline handlers ------------
    for idx, item in enumerate(probe.get("inline_key_handlers") or []):
        src = item.get("source") or ""
        if not src:
            continue
        if not _SINGLE_KEY_SRC.search(src):
            continue
        if _handler_has_modifier_guard(src):
            # Has a Ctrl/Alt/Meta guard → not a bare character shortcut.
            continue
        issues.append(make_issue(
            issue_id=f"char-key-handler-{idx}",
            module="char_key_shortcuts",
            rule="char-key-shortcut-single-key-handler",
            severity="moderate",
            wcag=["2.1.4"],
            confidence="low",
            title="Global key handler appears to bind a single character key",
            description=(
                "A keyboard event handler attached to "
                f"{item.get('target', 'the document')} keys off a single "
                "character with no modifier-key check in its inline "
                "source. Such handlers fire no matter where focus is, "
                "which WCAG 2.1.4 (A) restricts. Heuristic — only inline "
                "handler source is inspected (listeners added via "
                "addEventListener are invisible to this audit), so "
                "confirm the binding and whether a turn-off / remap / "
                "focus-scope mechanism is present."
            ),
            selector=item.get("selector", ""),
            html_snippet=item.get("html", ""),
            details={
                "target": item.get("target", ""),
                "attribute": item.get("attribute", ""),
                "source_excerpt": src[:200],
            },
            fix=(
                "Require a modifier key alongside the character, gate the "
                "shortcut behind a user-toggleable setting, or only act "
                "on the key while the relevant component has focus."
            ),
        ))

    return issues


# Probe: collect accesskey attributes and inline key-handler source from
# document/body/window-level elements. We keep the scan bounded and only
# read attributes already present in the DOM — no code execution.
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

    // 1. accesskey attributes (bounded scan).
    const accesskeys = [];
    const akEls = document.querySelectorAll('[accesskey]');
    let scanned = 0;
    for (const el of akEls) {
        if (scanned >= 200) break;
        scanned += 1;
        const ak = el.getAttribute('accesskey');
        if (!ak) continue;
        accesskeys.push({
            accesskey: ak,
            tag: el.tagName.toLowerCase(),
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
        });
        if (accesskeys.length >= 100) break;
    }

    // 2. Inline key-handler source on document-level surfaces + any
    // element carrying an inline onkeydown/onkeypress/onkeyup attribute.
    // We read the *attribute string* (handler source), never execute it.
    const inline_key_handlers = [];
    const KEY_ATTRS = ['onkeydown', 'onkeypress', 'onkeyup'];

    // Document-level surfaces: <body>, <html>. window/document inline
    // handlers surface as attributes on <body> in practice.
    const surfaces = [document.body, document.documentElement].filter(Boolean);
    for (const el of surfaces) {
        for (const attr of KEY_ATTRS) {
            const src = el.getAttribute && el.getAttribute(attr);
            if (src) {
                inline_key_handlers.push({
                    target: el.tagName.toLowerCase(),
                    attribute: attr,
                    source: src.slice(0, 1000),
                    selector: cssPath(el),
                    html: '<' + el.tagName.toLowerCase() + ' ' + attr + '="...">',
                });
            }
        }
    }

    // Any other element with an inline key handler (bounded).
    const handlerEls = document.querySelectorAll(
        '[onkeydown], [onkeypress], [onkeyup]'
    );
    let hScanned = 0;
    for (const el of handlerEls) {
        if (hScanned >= 200) break;
        hScanned += 1;
        if (el === document.body || el === document.documentElement) continue;
        for (const attr of KEY_ATTRS) {
            const src = el.getAttribute(attr);
            if (src) {
                inline_key_handlers.push({
                    target: el.tagName.toLowerCase(),
                    attribute: attr,
                    source: src.slice(0, 1000),
                    selector: cssPath(el),
                    html: (el.outerHTML || '').slice(0, 200),
                });
            }
        }
        if (inline_key_handlers.length >= 100) break;
    }

    return {accesskeys, inline_key_handlers};
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("char_key_shortcuts probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(probe or {})
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "accesskey_candidates": len((probe or {}).get("accesskeys") or []),
        "inline_handler_candidates": len((probe or {}).get("inline_key_handlers") or []),
    }
