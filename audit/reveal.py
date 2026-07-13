"""Interaction reveal — actuate collapsed UI, then audit what appears.

Most of the misses a static single-snapshot audit produces cluster on
ONE root cause: the page hides interactive UI behind a control we never
operate. A hamburger menu, an accordion, a tab strip, a slideshow — the
findings inside them (undersized targets, unnamed controls, missing
state) are invisible until something is clicked.

This module has two layers:

  **Layer 1 — disclosure state (always on, deterministic).**
  Detect toggle controls (menu / accordion / dropdown / disclosure) that
  reveal a hidden region but expose NO `aria-expanded`. A screen-reader
  user is never told whether the thing is open or closed. This is the
  "Mobile Menu: no announcement of state" / "Accordion controls do not
  announce state" finding, and it's detectable from markup alone — the
  defect is the *missing attribute*, so no clicking is required.
  Emits `disclosure-missing-expanded-state` (WCAG 4.1.2).

  **Layer 2 — actuation (opt-in via `options["reveal"]`).**
  Click each safe trigger, wait for the DOM to settle, then diff the set
  of visible interactive elements. Anything newly revealed is measured
  for two failures the static pass couldn't see:
    - `reveal-undersized-target` (WCAG 2.5.8) — a now-visible control
      smaller than 24x24 CSS px (e.g. 10x10 slideshow pagination dots).
    - `reveal-control-unnamed` (WCAG 4.1.2) — a now-visible interactive
      element with no accessible name.
    - `menu-focus-not-trapped` (WCAG 2.4.3) — the first interaction
      "recipe": for an overlay-style menu, open it, drive Tab, and flag
      when focus escapes onto the page behind. Gated to modal-like menus
      (a positioned overlay covering the page, or one with a backdrop) so
      non-modal inline dropdowns never false-positive.
  Triggers are clicked best-effort with per-trigger error isolation, and
  state is restored (Escape, then re-click) so later modules see a clean
  page. Navigating `<a href>` controls are never clicked.

Layer 2 is opt-in because clicking mutates the page; the default audit
stays read-only and reproducible. The pure analysis functions
(`analyze_triggers`, `analyze_revealed`) take JS-extracted snapshots, so
unit tests need no browser.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)

# Class tokens that mark an element as a disclosure / menu / accordion
# trigger. Bordered by word boundaries so "menu" matches `nav-menu` but
# the regex still rejects unrelated substrings like "menumberx".
_TOGGLE_CLASS = re.compile(
    r"(?:^|[\s_-])(?:menu|nav-?toggle|navbar-?toggle|hamburger|"
    r"accord(?:ion)?s?|accord-link|dropdown|disclosure|collapse|"
    r"collapsible|expander|toggle|offcanvas|drawer)(?:[\s_-]|$)",
    re.IGNORECASE,
)

# Overlay-style menu triggers (mobile nav / off-canvas / drawer). These
# reveal a region that visually covers the page, so — like a modal — Tab
# should stay inside until the menu is closed. Accordions and inline
# dropdowns are intentionally NOT here: they reflow content rather than
# overlay it, and ARIA APG does not require them to trap focus.
_MENU_CLASS = re.compile(
    r"(?:^|[\s_-])(?:menu|nav-?toggle|navbar-?toggle|hamburger|"
    r"offcanvas|drawer|slideout|sidenav)(?:[\s_-]|$)",
    re.IGNORECASE,
)

# Min target size (CSS px) for WCAG 2.5.8 (Minimum).
MIN_TARGET = 24.0

# How many Tab presses to probe before concluding a menu traps focus.
FOCUS_TRAP_TAB_BUDGET = 12


def _is_toggle_classed(classes: str) -> bool:
    return bool(_TOGGLE_CLASS.search(classes or ""))


def _is_menu_classed(classes: str) -> bool:
    return bool(_MENU_CLASS.search(classes or ""))


def _is_modal_like(
    position: str, w: float, h: float, vw: float, vh: float, backdrop: bool
) -> bool:
    """True when an opened region behaves like a modal overlay — the only
    case where a focus trap is required.

    A positioned region (fixed/absolute/sticky) that covers a large slice
    of the viewport, or any region accompanied by a visible backdrop, is
    modal-like. An inline region that reflows page content (static
    positioning, no backdrop) is a non-modal disclosure and needs no trap.
    """
    if backdrop:
        return True
    if (position or "").lower() not in ("fixed", "absolute", "sticky"):
        return False
    try:
        covers = (w >= 0.5 * vw) or (h >= 0.6 * vh)
    except (TypeError, ValueError):
        return False
    return bool(covers)


def analyze_focus_trap(membership: list[bool]) -> bool:
    """Given whether focus stayed inside the menu after each Tab press,
    return True if focus ESCAPED (a trap failure).

    `membership` is recorded only after focus is first placed inside the
    menu, so any False means Tab reached the page behind while the menu
    was open.
    """
    return bool(membership) and (False in membership)


def analyze_keyboard_operable(revealed_by_click, revealed_by_key) -> bool:
    """Return True when a control is keyboard-INOPERABLE: clicking it with
    the mouse revealed content, but pressing Enter/Space (with the control
    focused) revealed nothing. WCAG 2.1.1."""
    return bool(revealed_by_click) and not bool(revealed_by_key)


def analyze_hover_menu(focus_reveals: bool, hover_reveals: bool) -> bool:
    """Return True when a submenu is HOVER-ONLY: it appears when the
    trigger is hovered with the mouse, but not when the trigger is focused
    from the keyboard. Its links are then unreachable by keyboard users
    (WCAG 2.1.1)."""
    return bool(hover_reveals) and not bool(focus_reveals)


def _is_custom_trigger(trigger: dict[str, Any]) -> bool:
    """True for non-native controls whose keyboard behaviour is worth
    testing. Native <button>/<summary> activate on Enter/Space by
    definition, and <a href> we never drive, so those are skipped —
    testing them would only add work and can't reveal a new failure."""
    tag = (trigger.get("tag") or "").lower()
    role = (trigger.get("role") or "").lower()
    return tag in ("div", "span", "li") or role == "button"


# ---------------------------------------------------------------------
# Layer 1: disclosure-state analysis (deterministic, no clicking).
# ---------------------------------------------------------------------


def analyze_triggers(triggers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag interactive disclosure triggers missing aria-expanded."""
    issues: list[dict[str, Any]] = []
    for idx, t in enumerate(triggers or []):
        if not t.get("interactive") or not t.get("visible"):
            continue
        # Already exposes state → not this failure (whether it toggles
        # correctly is Layer 2 / the dynamic DSL's job).
        if t.get("aria_expanded") is not None:
            continue
        controls_hidden = bool(t.get("controls_exists") and t.get("controls_hidden"))
        class_signal = _is_toggle_classed(t.get("classes") or "")
        if not (controls_hidden or class_signal):
            continue
        # aria-controls → a hidden region is a strong signal; a class-only
        # match is suggestive.
        confidence = "medium" if controls_hidden else "low"
        name = (t.get("name") or "").strip() or "(no accessible name)"
        issues.append(make_issue(
            issue_id=f"disclosure-missing-expanded-{idx}",
            module="reveal",
            rule="disclosure-missing-expanded-state",
            severity="moderate",
            wcag=["4.1.2"],
            confidence=confidence,
            title=f"Toggle control {name!r} does not expose expanded/collapsed state",
            description=(
                "This control opens or closes a region (menu, accordion, "
                "dropdown) but has no aria-expanded attribute, so assistive "
                "tech never announces whether it is open or collapsed. WCAG "
                "4.1.2 (A) requires the state be programmatically exposed. "
                + ("It points at a currently-hidden region via aria-controls. "
                   if controls_hidden else
                   "Detected from a menu/accordion-style class — confirm it is "
                   "a real toggle. ")
                + "Add aria-expanded=\"false\"/\"true\" and keep it in sync."
            ),
            selector=t.get("selector", ""),
            html_snippet=t.get("html", ""),
            details={
                "classes": t.get("classes", ""),
                "aria_controls": t.get("aria_controls"),
                "controls_hidden": controls_hidden,
            },
            fix=(
                "Put aria-expanded on the trigger (false when closed, true "
                "when open) and toggle it in the click handler. Use a "
                "<button> for the trigger and aria-controls to point at the "
                "region it reveals."
            ),
        ))
    return issues


# ---------------------------------------------------------------------
# Layer 2: analysis of newly-revealed elements after a click.
# ---------------------------------------------------------------------


def _is_undersized(el: dict[str, Any]) -> bool:
    w = float(el.get("w") or 0)
    h = float(el.get("h") or 0)
    if w <= 0 or h <= 0:
        return False
    if el.get("inline_exception"):
        return False  # inline link in text flow — 2.5.8 exception
    return w < MIN_TARGET or h < MIN_TARGET


def analyze_revealed(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    trigger_name: str = "",
    start_idx: int = 0,
) -> list[dict[str, Any]]:
    """Diff visible-interactive snapshots; flag newly-revealed failures."""
    before_keys = {e.get("selector") for e in before}
    issues: list[dict[str, Any]] = []
    idx = start_idx
    for el in after:
        sel = el.get("selector")
        if not sel or sel in before_keys:
            continue  # was already visible — not revealed by this click
        name = (el.get("name") or "").strip()
        ctx = f" (revealed by {trigger_name!r})" if trigger_name else ""

        if _is_undersized(el):
            issues.append(make_issue(
                issue_id=f"reveal-undersized-{idx}",
                module="reveal",
                rule="reveal-undersized-target",
                severity="moderate",
                wcag=["2.5.8"],
                confidence="medium",
                title=(
                    f"Revealed control is {float(el.get('w') or 0):.0f}x"
                    f"{float(el.get('h') or 0):.0f}px (min 24x24)"
                ),
                description=(
                    "After actuating a toggle, this control became visible "
                    "but is smaller than the 24x24 CSS px minimum"
                    f"{ctx}. Small targets (e.g. slideshow pagination dots) "
                    "are hard to hit for users with motor impairments. WCAG "
                    "2.5.8 (AA). Only visible once the UI is opened, so a "
                    "static scan misses it."
                ),
                selector=sel,
                html_snippet=el.get("html", ""),
                details={
                    "width": el.get("w"), "height": el.get("h"),
                    "revealed_by": trigger_name,
                },
                fix="Make the target at least 24x24 CSS px, or add spacing.",
            ))
            idx += 1

        if el.get("interactive") and not name:
            issues.append(make_issue(
                issue_id=f"reveal-unnamed-{idx}",
                module="reveal",
                rule="reveal-control-unnamed",
                severity="serious",
                wcag=["4.1.2"],
                confidence="medium",
                title="Revealed interactive control has no accessible name",
                description=(
                    "After actuating a toggle, this interactive element "
                    f"became visible but has no accessible name{ctx}. Screen-"
                    "reader users hear an unlabeled control. WCAG 4.1.2 (A). "
                    "Only present once the UI is opened, so a static scan "
                    "misses it."
                ),
                selector=sel,
                html_snippet=el.get("html", ""),
                details={"revealed_by": trigger_name},
                fix=(
                    "Give the control text content, aria-label, or "
                    "aria-labelledby."
                ),
            ))
            idx += 1
    return issues


# ---------------------------------------------------------------------
# Probes.
# ---------------------------------------------------------------------

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
    function nameOf(el) {
        return (el.getAttribute('aria-label')
            || (el.textContent || '').trim()
            || el.getAttribute('title')
            || (el.querySelector('img') && el.querySelector('img').getAttribute('alt'))
            || '').trim().slice(0, 80);
    }
    function isHidden(el) {
        if (!el) return true;
        if (el.hasAttribute('hidden')) return true;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') return true;
        const r = el.getBoundingClientRect();
        return r.width < 1 || r.height < 1;
    }

    const out = [];
    // Candidate triggers: anything with toggle semantics or a toggle-ish
    // class on an interactive element.
    const sel = '[aria-expanded],[aria-controls],[aria-haspopup],button,'
        + '[role=button],a,[onclick],[class*="menu"],[class*="toggle"],'
        + '[class*="accordion"],[class*="dropdown"],[class*="hamburger"],'
        + '[data-toggle],[data-target],[data-bs-toggle],[data-bs-target]';
    const els = document.querySelectorAll(sel);
    let scanned = 0;
    for (const el of els) {
        if (scanned >= 1500) break;
        scanned += 1;
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role');
        const cs = getComputedStyle(el);
        const tabbable = el.tabIndex >= 0;
        const interactive = (
            tag === 'button' || tag === 'summary'
            || (tag === 'a')
            || role === 'button' || role === 'menuitem'
            || el.hasAttribute('onclick') || cs.cursor === 'pointer' || tabbable
        );
        if (!interactive) continue;
        const controlsId = el.getAttribute('aria-controls');
        let controlsExists = false, controlsHidden = false;
        if (controlsId) {
            const tgt = document.getElementById(controlsId);
            controlsExists = !!tgt;
            controlsHidden = tgt ? isHidden(tgt) : false;
        }
        // Bootstrap-style disclosure: data-target / data-bs-target hold a
        // CSS selector (".navbar-collapse") or "#id" for the region the
        // control reveals. Resolve it the same way as aria-controls so
        // the "controls a hidden region" signal works on Bootstrap sites.
        const dataTarget = el.getAttribute('data-target')
            || el.getAttribute('data-bs-target');
        const dataToggle = el.getAttribute('data-toggle')
            || el.getAttribute('data-bs-toggle');
        if (!controlsExists && dataTarget) {
            try {
                const tgt = document.querySelector(dataTarget);
                if (tgt) { controlsExists = true; controlsHidden = isHidden(tgt); }
            } catch (e) { /* invalid selector — ignore */ }
        }
        // A CSS selector for the revealed region, used by the focus-trap
        // recipe to test containment. Prefer the id form for aria-controls.
        let controlsSelector = null;
        if (controlsId) {
            controlsSelector = '#' + ((window.CSS && CSS.escape)
                ? CSS.escape(controlsId) : controlsId);
        } else if (dataTarget) {
            controlsSelector = dataTarget;
        }
        const r = el.getBoundingClientRect();
        out.push({
            tag, role,
            name: nameOf(el),
            aria_expanded: el.getAttribute('aria-expanded'),  // null if absent
            aria_controls: controlsId,
            aria_haspopup: el.getAttribute('aria-haspopup'),
            controls_exists: controlsExists,
            controls_hidden: controlsHidden,
            controls_selector: controlsSelector,
            classes: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 120),
            interactive: true,
            navigates: tag === 'a' && el.hasAttribute('href')
                && !(el.getAttribute('href') || '').startsWith('#'),
            visible: r.width >= 1 && r.height >= 1 && cs.visibility !== 'hidden' && cs.display !== 'none',
            selector: cssPath(el),
            html: (el.outerHTML || '').slice(0, 200),
        });
        if (out.length >= 200) break;
    }
    return out;
}
"""

# Snapshot every currently-visible interactive element (for the Layer 2
# before/after diff).
_CAPTURE_JS = r"""
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
    function nameOf(el) {
        return (el.getAttribute('aria-label')
            || (el.textContent || '').trim()
            || el.getAttribute('title')
            || (el.querySelector('img') && el.querySelector('img').getAttribute('alt'))
            || (el.tagName === 'INPUT' ? (el.getAttribute('value') || '') : '')
            || '').trim().slice(0, 80);
    }
    const out = [];
    const sel = 'a[href],button,input,select,textarea,summary,[role=button],'
        + '[role=menuitem],[role=tab],[role=link],[tabindex]:not([tabindex^="-"])';
    const els = document.querySelectorAll(sel);
    let n = 0;
    for (const el of els) {
        if (n >= 3000) break;
        n += 1;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        const tag = el.tagName.toLowerCase();
        // Inline link sitting inside a paragraph/list text flow → 2.5.8
        // inline exception, so don't treat as an undersized target.
        const inlineException = tag === 'a'
            && !!el.closest('p, li, td, span')
            && (el.textContent || '').trim().length > 0;
        out.push({
            selector: cssPath(el),
            tag,
            w: Math.round(r.width), h: Math.round(r.height),
            name: nameOf(el),
            interactive: true,
            inline_exception: inlineException,
            html: (el.outerHTML || '').slice(0, 160),
        });
    }
    return out;
}
"""


# Focus-trap recipe probes. Kept as module constants so the driving
# happens in Python (Tab presses between evaluations) while each JS
# snippet is a single pure query.
_MODAL_LIKE_JS = r"""
(sel) => {
    let c;
    try { c = document.querySelector(sel); } catch (e) { return {ok: false}; }
    if (!c) return {ok: false};
    const cs = getComputedStyle(c);
    if (cs.display === 'none' || cs.visibility === 'hidden') return {ok: false};
    const r = c.getBoundingClientRect();
    const backdrop = !!document.querySelector(
        '.modal-backdrop, .offcanvas-backdrop, [class*="backdrop"], [class*="overlay"]'
    );
    return {ok: true, position: cs.position, w: r.width, h: r.height,
            vw: window.innerWidth, vh: window.innerHeight, backdrop};
}
"""

_FOCUS_FIRST_IN_JS = r"""
(sel) => {
    let c;
    try { c = document.querySelector(sel); } catch (e) { return false; }
    if (!c) return false;
    const f = c.querySelector(
        'a[href],button,input,select,textarea,[tabindex]:not([tabindex^="-"])'
    );
    if (!f) return false;
    f.focus();
    return document.activeElement === f;
}
"""

_ACTIVE_INSIDE_JS = r"""
(sel) => {
    const active = document.activeElement;
    if (!active || active === document.body) return false;
    let containers;
    try { containers = document.querySelectorAll(sel); }
    catch (e) { return true; }  // bad selector → fail-open, don't false-positive
    for (const c of containers) { if (c.contains(active)) return true; }
    return false;
}
"""

_FOCUS_TRIGGER_JS = r"""
(sel) => {
    let el;
    try { el = document.querySelector(sel); } catch (e) { return false; }
    if (!el || typeof el.focus !== 'function') return false;
    el.focus();
    return document.activeElement === el;
}
"""

_ELEM_VISIBLE_JS = r"""
(sel) => {
    let el;
    try { el = document.querySelector(sel); } catch (e) { return false; }
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
}
"""

# Find nav dropdowns whose submenu is hidden at rest — candidates for the
# hover-only-menu recipe.
_HOVER_MENU_FIND_JS = r"""
() => {
    function cssPath(el) {
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
    const out = [], seen = new Set();
    const lis = document.querySelectorAll(
        'nav li, [class*="nav"] li, [class*="menu"] li, header li'
    );
    let scanned = 0;
    for (const li of lis) {
        if (scanned >= 300) break;
        scanned += 1;
        const sub = li.querySelector(':scope > ul, :scope > [class*="submenu"], :scope > [class*="dropdown"]');
        const trigger = li.querySelector(':scope > a, :scope > button');
        if (!sub || !trigger) continue;
        const cs = getComputedStyle(sub);
        const hidden = cs.display === 'none' || cs.visibility === 'hidden'
            || parseFloat(cs.opacity) === 0;
        if (!hidden) continue;  // only submenus hidden at rest
        const links = sub.querySelectorAll('a[href],button').length;
        if (links < 1) continue;
        const tsel = cssPath(trigger), ssel = cssPath(sub);
        if (!tsel || !ssel || seen.has(tsel)) continue;
        seen.add(tsel);
        out.push({
            trigger_selector: tsel, submenu_selector: ssel,
            sub_link_count: links,
            trigger_text: (trigger.textContent || '').trim().slice(0, 30),
            html: (trigger.outerHTML || '').slice(0, 140),
        });
        if (out.length >= 8) break;
    }
    return out;
}
"""


def _observe_hover_menus(page) -> list[dict[str, Any]]:
    """Flag nav submenus that appear on mouse hover but not on keyboard
    focus — their links are unreachable by keyboard (WCAG 2.1.1)."""
    try:
        menus = page.evaluate(_HOVER_MENU_FIND_JS) or []
    except Exception:
        log.debug("hover-menu find probe failed", exc_info=True)
        return []
    issues: list[dict[str, Any]] = []
    for idx, m in enumerate(menus):
        tsel = m.get("trigger_selector")
        ssel = m.get("submenu_selector")
        if not tsel or not ssel:
            continue
        try:
            # Keyboard: focus the trigger, does the submenu appear?
            page.evaluate(_FOCUS_TRIGGER_JS, tsel)
            page.wait_for_timeout(120)
            focus_reveals = bool(page.evaluate(_ELEM_VISIBLE_JS, ssel))
            page.evaluate(
                "() => document.activeElement && document.activeElement.blur "
                "&& document.activeElement.blur()"
            )
            page.wait_for_timeout(60)
            # Mouse: hover the trigger, does the submenu appear?
            page.locator(tsel).first.hover(timeout=2000)
            page.wait_for_timeout(150)
            hover_reveals = bool(page.evaluate(_ELEM_VISIBLE_JS, ssel))
            page.mouse.move(0, 0)
            page.wait_for_timeout(60)
        except Exception:
            log.debug("hover-menu probe failed for %s", tsel, exc_info=True)
            continue

        if not analyze_hover_menu(focus_reveals, hover_reveals):
            continue
        name = (m.get("trigger_text") or "").strip() or "menu"
        n = m.get("sub_link_count")
        issues.append(make_issue(
            issue_id=f"submenu-keyboard-inaccessible-{idx}",
            module="reveal",
            rule="submenu-keyboard-inaccessible",
            severity="serious",
            wcag=["2.1.1"],
            confidence="medium",
            title=f"Submenu under {name!r} opens on hover but not keyboard focus",
            description=(
                f"This dropdown's submenu ({n} link"
                f"{'s' if n != 1 else ''}) becomes visible when the trigger "
                "is hovered with a mouse, but stays hidden when the trigger "
                "is focused from the keyboard — so keyboard-only and "
                "screen-reader users cannot reach those links at all. WCAG "
                "2.1.1 (A). Confirmed by actuation — hover and keyboard "
                "focus were both driven and compared. The submenu is "
                "revealed by a :hover CSS rule with no :focus / "
                ":focus-within equivalent."
            ),
            selector=tsel,
            html_snippet=m.get("html", ""),
            details={
                "submenu": ssel,
                "sub_link_count": n,
            },
            fix=(
                "Reveal the submenu on :focus-within as well as :hover (or "
                "with JS on focusin/focusout), so keyboard focus into the "
                "menu shows it. A disclosure <button> with aria-expanded is "
                "the robust pattern."
            ),
        ))
    return issues


def _capture_selectors(page) -> set:
    """Set of selectors for every currently-visible interactive element."""
    try:
        return {e.get("selector") for e in (page.evaluate(_CAPTURE_JS) or [])}
    except Exception:
        return set()


def _check_keyboard_operable(page, sel: str, revealed_click: set) -> set | None:
    """Precondition: the menu the click opened has been closed by the
    caller. Focus the trigger and try Enter, then Space, returning the set
    of newly-revealed interactive selectors. Returns None when we can't
    run a clean test (trigger not focusable, or the menu is still open) —
    those are either a different failure (fake_button) or untestable, and
    we never want a false 2.1.1."""
    try:
        before = _capture_selectors(page)
        # If the click-revealed content is still showing, the menu didn't
        # close — a keyboard test would be meaningless.
        if revealed_click & before:
            return None
        if not page.evaluate(_FOCUS_TRIGGER_JS, sel):
            return None  # not focusable → fake_button's territory, not ours
        page.keyboard.press("Enter")
        page.wait_for_timeout(180)
        revealed = _capture_selectors(page) - before
        if not revealed:
            page.keyboard.press(" ")
            page.wait_for_timeout(180)
            revealed = _capture_selectors(page) - before
    except Exception:
        return None
    # Close whatever the keyboard opened so later state stays clean.
    if revealed:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
        except Exception:
            pass
    return revealed


def _check_menu_focus_trap(page, container_sel: str) -> list[bool] | None:
    """Drive Tab inside an opened modal-like menu and report, after each
    press, whether focus is still inside it. Returns None when the menu
    is not modal-like (no trap required) or can't be probed."""
    try:
        info = page.evaluate(_MODAL_LIKE_JS, container_sel)
    except Exception:
        return None
    if not info or not info.get("ok"):
        return None
    if not _is_modal_like(
        info.get("position", ""), info.get("w", 0), info.get("h", 0),
        info.get("vw", 1), info.get("vh", 1), bool(info.get("backdrop")),
    ):
        return None
    try:
        if not page.evaluate(_FOCUS_FIRST_IN_JS, container_sel):
            return None
    except Exception:
        return None
    membership: list[bool] = []
    for _ in range(FOCUS_TRAP_TAB_BUDGET):
        try:
            page.keyboard.press("Tab")
            page.wait_for_timeout(30)
            inside = bool(page.evaluate(_ACTIVE_INSIDE_JS, container_sel))
        except Exception:
            inside = True  # fail-open on infra hiccups; never false-positive
        membership.append(inside)
        if not inside:
            break
    return membership


# ---------------------------------------------------------------------
# Recipe: carousel auto-advance (WCAG 2.2.1). The "observe a state delta
# over time" shape — no input driven, we just watch whether the active
# slide changes on a timer with no user action.
# ---------------------------------------------------------------------

# How long to watch a carousel before concluding it does not auto-advance.
CAROUSEL_POLL_MS = 500
CAROUSEL_MAX_SAMPLES = 10  # 10 x 500ms = up to 5s, early-exit on first change

_CAROUSEL_FIND_JS = r"""
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
    const SEL = '[class*="carousel"],[class*="slider"],[class*="slideshow"],'
        + '[class*="swiper"],[class*="slick"],[class*="glide"],'
        + '[aria-roledescription="carousel"]';
    const SLIDES = '[class*="slide"],[class*="item"],li,[role="group"],[role="tabpanel"]';
    const out = [], seen = new Set();
    for (const c of document.querySelectorAll(SEL)) {
        const slides = c.querySelectorAll(SLIDES);
        if (slides.length < 2) continue;
        const p = cssPath(c);
        if (!p || seen.has(p)) continue;
        seen.add(p);
        const has_pause = !!c.querySelector(
            '[aria-label*="pause" i],[aria-label*="stop" i],'
            + '[class*="pause"],[class*="play"],button[title*="pause" i]'
        );
        out.push({selector: p, has_pause, slide_count: slides.length});
        if (out.length >= 3) break;
    }
    return out;
}
"""

# Returns {slide, live}: the carousel's current slide signature AND a
# signature of every live region on the page. Comparing the live
# signature before/after a slide change tells us whether the change was
# announced to assistive tech (WCAG 4.1.3).
_CAROUSEL_STATE_JS = r"""
(sel) => {
    let c;
    try { c = document.querySelector(sel); } catch (e) { return {slide: '', live: ''}; }
    if (!c) return {slide: '', live: ''};
    const SLIDES = '[class*="slide"],[class*="item"],li,[role="group"],[role="tabpanel"]';
    const slides = c.querySelectorAll(SLIDES);
    let activeIdx = -1;
    slides.forEach((s, i) => {
        const cls = (s.className && s.className.toString ? s.className.toString() : '');
        if (/active|current|selected|is-selected|is-active/i.test(cls)) activeIdx = i;
    });
    const hidden = [...slides].map(s => s.getAttribute('aria-hidden') || '').join('');
    const slide = activeIdx + '|' + Math.round((c.scrollLeft || 0) / 40)
        + '|' + hidden.slice(0, 60);
    // Live-region content across the page. A polite/assertive region (or
    // role=status/alert/log) that updates when the slide changes is the
    // announcement mechanism 4.1.3 asks for.
    let live = '';
    document.querySelectorAll(
        '[aria-live="polite"],[aria-live="assertive"],[role="status"],'
        + '[role="alert"],[role="log"]'
    ).forEach(l => { live += (l.textContent || '').trim().slice(0, 40) + '~'; });
    return {slide, live: live.slice(0, 400)};
}
"""


# Static structural probe for a carousel: whether it has an accessible
# name (label or heading) and the sizes of its pagination dots. Runs in
# Layer 1 (no actuation needed).
_CAROUSEL_STRUCT_JS = r"""
() => {
    function cssPath(el) {
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
    const SEL = '[class*="carousel"],[class*="slider"],[class*="slideshow"],'
        + '[class*="swiper"],[class*="slick"],[class*="glide"],'
        + '[aria-roledescription="carousel"]';
    const SLIDES = '[class*="slide"],[class*="item"],li,[role="group"],[role="tabpanel"]';
    const out = [], seen = new Set();
    for (const c of document.querySelectorAll(SEL)) {
        if (c.querySelectorAll(SLIDES).length < 2) continue;
        const p = cssPath(c);
        if (!p || seen.has(p)) continue;
        seen.add(p);
        const has_label = !!(c.getAttribute('aria-label') || '').trim()
            || !!c.getAttribute('aria-labelledby')
            || !!c.getAttribute('title');
        const has_heading = !!c.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
        // Pagination dots: search the carousel and its parent (dots often
        // sit as a sibling of the slide track).
        const scope = c.parentElement || c;
        const dots = [];
        const dotEls = scope.querySelectorAll(
            '[class*="dot"],[class*="bullet"],[class*="indicator"],'
            + '[class*="pager"] > *,[class*="pagination"] > *'
        );
        for (const d of dotEls) {
            const cs = getComputedStyle(d);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            const r = d.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) continue;
            dots.push({
                w: Math.round(r.width), h: Math.round(r.height),
                focusable: d.tabIndex >= 0,
                tag: d.tagName.toLowerCase(),
                selector: cssPath(d), html: (d.outerHTML || '').slice(0, 120),
            });
            if (dots.length >= 12) break;
        }
        out.push({selector: p, has_label, has_heading, dots,
                  html: (c.outerHTML || '').slice(0, 140)});
        if (out.length >= 5) break;
    }
    return out;
}
"""


def analyze_carousel_structure(carousels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Static carousel checks: unlabeled region (1.3.1) and undersized
    pagination dots (2.5.8). Pure over the struct probe."""
    issues: list[dict[str, Any]] = []
    for idx, c in enumerate(carousels or []):
        if not c.get("has_label") and not c.get("has_heading"):
            issues.append(make_issue(
                issue_id=f"carousel-region-no-name-{idx}",
                module="reveal",
                rule="carousel-region-no-name",
                severity="moderate",
                wcag=["1.3.1"],
                confidence="low",
                title="Carousel/slideshow region has no accessible name",
                description=(
                    "This slideshow is a distinct block of content with no "
                    "heading and no aria-label / aria-labelledby, so a "
                    "screen-reader user navigating by region or heading "
                    "cannot find or identify it. WCAG 1.3.1 (A). Heuristic "
                    "— add a label only if the region is genuinely a "
                    "landmark-worthy section."
                ),
                selector=c.get("selector", ""),
                html_snippet=c.get("html", ""),
                details={},
                fix=(
                    "Give the carousel a name: aria-label (e.g. "
                    "\"Featured programs\"), aria-labelledby pointing at a "
                    "visible heading, or a real heading inside it. "
                    "aria-roledescription=\"carousel\" also helps."
                ),
            ))
        dots = c.get("dots") or []
        # Pagination controls that are not keyboard-focusable — a keyboard
        # user can't select a slide (WCAG 2.1.1). Guard: only fire when at
        # least two such controls exist (a real pager), and none of them is
        # focusable, to avoid flagging decorative dots on a page that also
        # has real prev/next buttons.
        non_focusable = [d for d in dots if not d.get("focusable")]
        if len(dots) >= 2 and len(non_focusable) == len(dots):
            issues.append(make_issue(
                issue_id=f"carousel-control-not-keyboard-{idx}",
                module="reveal",
                rule="carousel-control-not-keyboard",
                severity="serious",
                wcag=["2.1.1"],
                confidence="medium",
                title=f"Carousel's {len(dots)} pagination controls are not keyboard-focusable",
                description=(
                    "The carousel's pagination dots / indicators cannot take "
                    "keyboard focus (they are non-interactive elements with "
                    "no tabindex), so a keyboard-only user cannot jump to a "
                    "slide. WCAG 2.1.1 (A). Use <button>s for the pagination "
                    "controls."
                ),
                selector=non_focusable[0].get("selector", ""),
                html_snippet=non_focusable[0].get("html", ""),
                details={"control_count": len(dots)},
                fix=(
                    "Make each pagination control a real <button> (focusable "
                    "and Enter/Space operable), wired to select its slide."
                ),
            ))

        small = [
            d for d in dots
            if 0 < min(d.get("w", 0), d.get("h", 0)) < 24
        ]
        if small:
            issues.append(make_issue(
                issue_id=f"carousel-control-undersized-{idx}",
                module="reveal",
                rule="carousel-control-undersized",
                severity="moderate",
                wcag=["2.5.8"],
                confidence="medium",
                title=(
                    f"Carousel has {len(small)} pagination control(s) "
                    f"smaller than 24x24px (e.g. {small[0]['w']}x{small[0]['h']}px)"
                ),
                description=(
                    "The carousel's pagination dots / indicators are below "
                    "the 24x24 CSS px minimum target size, so users with "
                    "motor impairments struggle to hit them. WCAG 2.5.8 "
                    "(AA)."
                ),
                selector=small[0].get("selector", ""),
                html_snippet=small[0].get("html", ""),
                details={
                    "undersized_count": len(small),
                    "sizes": [f"{d['w']}x{d['h']}" for d in small[:6]],
                },
                fix=(
                    "Make each pagination control at least 24x24 CSS px "
                    "(enlarge the hit area with padding even if the visible "
                    "dot stays small), or provide 24px spacing between them."
                ),
            ))
    return issues


def analyze_carousel_samples(
    samples: list[str], sample_interval_s: float
) -> dict[str, Any]:
    """Given signatures sampled over time (no user interaction between
    samples), decide whether the carousel auto-advanced and after how
    long. The first sample that differs from the initial state is the
    advance; its index times the poll interval approximates the timer.
    """
    if not samples:
        return {"auto_advance": False, "interval_s": None}
    first = samples[0]
    for i in range(1, len(samples)):
        if samples[i] != first:
            return {"auto_advance": True, "interval_s": round(i * sample_interval_s, 1)}
    return {"auto_advance": False, "interval_s": None}


def analyze_carousel_announcement(
    slide_samples: list[str], live_samples: list[str]
) -> dict[str, Any]:
    """Given slide + live-region signatures over time, decide whether a
    slide change happened and, if so, whether a live region announced it.

    `announced` is True when the live-region signature differed at the
    same sample the slide changed — i.e. content was pushed to a live
    region when the slide advanced.
    """
    if not slide_samples:
        return {"changed": False, "announced": None}
    first_slide = slide_samples[0]
    first_live = live_samples[0] if live_samples else ""
    for i in range(1, len(slide_samples)):
        if slide_samples[i] != first_slide:
            announced = i < len(live_samples) and live_samples[i] != first_live
            return {"changed": True, "announced": bool(announced)}
    return {"changed": False, "announced": None}


def _observe_carousels(page) -> list[dict[str, Any]]:
    """Watch each carousel for a timer-driven slide change and flag any
    that auto-advance without a pause/stop control (WCAG 2.2.1)."""
    try:
        carousels = page.evaluate(_CAROUSEL_FIND_JS) or []
    except Exception:
        log.debug("carousel find probe failed", exc_info=True)
        return []
    issues: list[dict[str, Any]] = []
    for idx, c in enumerate(carousels):
        sel = c.get("selector")
        if not sel:
            continue
        slide_samples: list[str] = []
        live_samples: list[str] = []
        try:
            for k in range(CAROUSEL_MAX_SAMPLES):
                state = page.evaluate(_CAROUSEL_STATE_JS, sel) or {}
                slide_samples.append(state.get("slide") or "")
                live_samples.append(state.get("live") or "")
                if len(slide_samples) >= 2 and slide_samples[-1] != slide_samples[0]:
                    break
                if k < CAROUSEL_MAX_SAMPLES - 1:
                    page.wait_for_timeout(CAROUSEL_POLL_MS)
        except Exception:
            log.debug("carousel observe failed for %s", sel, exc_info=True)
            continue

        result = analyze_carousel_samples(slide_samples, CAROUSEL_POLL_MS / 1000.0)
        if not result["auto_advance"]:
            continue

        # WCAG 2.2.1 — auto-advance with no pause/stop control. A pause
        # control satisfies 2.2.1 (the user can stop the timer), so we
        # only flag when none was found.
        if not c.get("has_pause"):
            interval = result["interval_s"]
            issues.append(make_issue(
                issue_id=f"carousel-auto-advance-{idx}",
                module="reveal",
                rule="carousel-auto-advance",
                severity="serious",
                wcag=["2.2.1"],
                confidence="medium",
                title=(
                    f"Carousel auto-advances (~{interval:g}s) with no pause control"
                    if interval else "Carousel auto-advances with no pause control"
                ),
                description=(
                    "This carousel changed slides on its own, with no user "
                    "action"
                    + (f" (observed after ~{interval:g}s)" if interval else "")
                    + ", and no pause / stop / play control was found. WCAG "
                    "2.2.1 (A) requires that a user can turn off, adjust, or "
                    "extend a moving time limit; a slide that rotates before "
                    "a slow reader finishes leaves them stranded. Confirmed "
                    "by observation — the page was watched, not just "
                    "inspected. (If a pause control exists but wasn't "
                    "detected, review and dismiss.)"
                ),
                selector=sel,
                html_snippet="",
                details={
                    "auto_advance_interval_s": interval,
                    "slide_count": c.get("slide_count"),
                },
                fix=(
                    "Add a visible, keyboard-operable pause/stop control, or "
                    "stop auto-rotation and let the user advance slides "
                    "themselves. Pausing on hover/focus alone is not enough."
                ),
            ))

        # WCAG 4.1.3 — the slide changed but no live region announced it,
        # so a screen-reader user is never told new content appeared.
        ann = analyze_carousel_announcement(slide_samples, live_samples)
        if ann["changed"] and not ann["announced"]:
            issues.append(make_issue(
                issue_id=f"carousel-change-not-announced-{idx}",
                module="reveal",
                rule="carousel-change-not-announced",
                severity="moderate",
                wcag=["4.1.3"],
                confidence="medium",
                title="Carousel slide change is not announced to screen readers",
                description=(
                    "The carousel advanced to a new slide, but no live "
                    "region (aria-live / role=status) updated when it did. "
                    "Screen-reader users are not told the content changed "
                    "underneath them. WCAG 4.1.3 (AA) asks that such status "
                    "changes be announced. Confirmed by observation — the "
                    "slide changed and the live regions did not. Real speech "
                    "confirmation requires the NVDA (Path B) worker."
                ),
                selector=sel,
                html_snippet="",
                details={"slide_count": c.get("slide_count")},
                fix=(
                    "Announce slide changes in an aria-live=\"polite\" "
                    "region (e.g. \"Slide 2 of 5\" or the new slide's "
                    "heading). Do not use aria-live=\"assertive\" for "
                    "routine rotation."
                ),
            ))
    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    start = time.time()
    issues: list[dict[str, Any]] = []
    triggers_seen = 0
    actuated = 0
    try:
        triggers = page.evaluate(_DISCOVER_JS) or []
    except Exception as exc:
        log.exception("reveal discover probe failed")
        return {
            "ran": False, "error": str(exc), "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    triggers_seen = len(triggers)
    # Layer 1 — always (read-only).
    issues.extend(analyze_triggers(triggers))
    # Static carousel structure: unlabeled region (1.3.1) + undersized
    # pagination dots (2.5.8). No actuation needed, so it runs by default.
    try:
        structs = page.evaluate(_CAROUSEL_STRUCT_JS) or []
        issues.extend(analyze_carousel_structure(structs))
    except Exception:
        log.debug("carousel structure probe failed", exc_info=True)

    # Layer 2 — opt-in actuation + timed observation recipes.
    if options.get("reveal"):
        issues.extend(_actuate(page, triggers))
        # Carousel auto-advance is cheap when no carousel exists (the
        # find probe returns nothing), and early-exits as soon as a slide
        # change is seen, so it only spends real time on pages that
        # actually have an auto-rotating carousel.
        issues.extend(_observe_carousels(page))
        # Hover-only nav submenus (keyboard-inaccessible dropdowns).
        issues.extend(_observe_hover_menus(page))
        actuated = 1

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "triggers_found": triggers_seen,
        "actuated": bool(actuated),
    }


def _actuate(page, triggers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Click safe triggers, diff visible-interactive sets, flag reveals."""
    issues: list[dict[str, Any]] = []
    # Only actuate triggers that (a) don't navigate away and (b) look
    # like disclosure controls — keep the click budget small.
    candidates = [
        t for t in triggers
        if not t.get("navigates")
        and (
            (t.get("controls_exists") and t.get("controls_hidden"))
            or _is_toggle_classed(t.get("classes") or "")
            or t.get("aria_expanded") is not None
        )
    ][:12]

    idx = 0
    for t in candidates:
        sel = t.get("selector")
        if not sel:
            continue
        try:
            before = page.evaluate(_CAPTURE_JS) or []
            loc = page.locator(sel).first
            loc.click(timeout=2500)
            page.wait_for_timeout(250)
            after = page.evaluate(_CAPTURE_JS) or []
        except Exception:
            log.debug("reveal: click failed for %s", sel, exc_info=True)
            continue

        before_sels = {e.get("selector") for e in before}
        revealed_click = {e.get("selector") for e in after} - before_sels

        new_issues = analyze_revealed(
            before, after,
            trigger_name=(t.get("name") or "").strip(),
            start_idx=idx,
        )
        issues.extend(new_issues)
        idx += len(new_issues) + 1

        # Focus-trap recipe: for an overlay-style menu (nav / off-canvas /
        # drawer) that reveals a resolvable region, drive Tab and check
        # focus stays inside. Gated to modal-like menus inside the check
        # so non-modal dropdowns (which need no trap) don't false-positive.
        container_sel = t.get("controls_selector")
        if container_sel and _is_menu_classed(t.get("classes") or ""):
            membership = _check_menu_focus_trap(page, container_sel)
            if membership is not None and analyze_focus_trap(membership):
                escape_after = membership.index(False) + 1
                name = (t.get("name") or "").strip() or "menu"
                issues.append(make_issue(
                    issue_id=f"menu-focus-trap-{idx}",
                    module="reveal",
                    rule="menu-focus-not-trapped",
                    severity="serious",
                    wcag=["2.4.3"],
                    confidence="medium",
                    title=f"Open menu {name!r} does not keep focus inside",
                    description=(
                        "This menu opens as an overlay covering the page, "
                        "but while it is open Tab moves focus out of the "
                        f"menu and onto the content behind it (after "
                        f"{escape_after} press(es)). Keyboard and screen-"
                        "reader users lose their place and can operate "
                        "hidden page controls. An overlay menu should keep "
                        "focus inside until it is closed (WCAG 2.4.3). "
                        "Detected by actuation — the menu was opened and "
                        "Tab was driven through it."
                    ),
                    selector=sel,
                    html_snippet=t.get("html", ""),
                    details={
                        "container": container_sel,
                        "tab_presses_to_escape": escape_after,
                    },
                    fix=(
                        "Trap focus while the overlay menu is open: keep "
                        "Tab/Shift+Tab within the menu, wrapping from the "
                        "last item to the first, and return focus to the "
                        "toggle on close. Escape should close the menu."
                    ),
                ))
                idx += 1

        # Restore: Escape, and if the page still differs, click again.
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(120)
            restored = page.evaluate(_CAPTURE_JS) or []
            if len(restored) > len(before):
                page.locator(sel).first.click(timeout=1500)
                page.wait_for_timeout(120)
        except Exception:
            log.debug("reveal: restore failed for %s", sel, exc_info=True)

        # Keyboard-operability recipe (WCAG 2.1.1): the click just revealed
        # content on a custom (non-native) control. Now — with the menu
        # closed by the restore above — focus the control and try
        # Enter/Space. If the mouse opened it but the keyboard can't, it's
        # mouse-only. Runs only for custom triggers; native buttons/links
        # activate on Enter by definition and are skipped.
        if revealed_click and _is_custom_trigger(t):
            revealed_key = _check_keyboard_operable(page, sel, revealed_click)
            if revealed_key is not None and analyze_keyboard_operable(
                revealed_click, revealed_key
            ):
                name = (t.get("name") or "").strip() or "control"
                issues.append(make_issue(
                    issue_id=f"keyboard-inoperable-{idx}",
                    module="reveal",
                    rule="keyboard-inoperable-control",
                    severity="serious",
                    wcag=["2.1.1"],
                    confidence="medium",
                    title=f"Control {name!r} works with a mouse but not the keyboard",
                    description=(
                        "Clicking this control with the mouse revealed "
                        "content (a menu / panel), but focusing it and "
                        "pressing Enter or Space did nothing. Keyboard-only "
                        "and switch users cannot operate it. WCAG 2.1.1 (A) "
                        "requires all functionality be available from the "
                        "keyboard. Confirmed by actuation — mouse and "
                        "keyboard were both driven and compared. Use a "
                        "native <button>, or add keydown handling for Enter "
                        "and Space."
                    ),
                    selector=sel,
                    html_snippet=t.get("html", ""),
                    details={"tag": t.get("tag"), "role": t.get("role")},
                    fix=(
                        "Use a native <button> for the trigger, or handle "
                        "keydown for Enter (and Space for button-like "
                        "controls) in addition to click."
                    ),
                ))
                idx += 1

    return issues
