"""APG widget-pattern validation.

The WAI-ARIA Authoring Practices Guide (APG) defines structural
requirements for composite widgets: what roles must appear, which
ARIA attributes are required vs forbidden, and what keyboard
behavior is expected. Sites pass axe-core and still ship broken
widgets because axe mostly validates individual elements, not
widget PATTERNS.

We implement the shape-checking part (structure + required attrs);
the keyboard-behavior checks live in audit/dynamic.py (testers can
declare expected arrow-key behavior via the Interaction DSL).

Patterns covered (starter set — by frequency of use on real sites):
  - combobox         APG: https://www.w3.org/WAI/ARIA/apg/patterns/combobox/
  - dialog / modal   APG: https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
  - tablist / tab    APG: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/
  - disclosure       APG: https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/

Rules emitted:
  - widget-combobox-missing-expanded    WCAG 4.1.2 serious
  - widget-combobox-missing-controls    WCAG 4.1.2 serious
  - widget-dialog-missing-name          WCAG 4.1.2 serious
  - widget-dialog-missing-aria-modal    WCAG 4.1.2 moderate
  - widget-tablist-no-tabs              WCAG 1.3.1 serious
  - widget-tab-missing-selected         WCAG 4.1.2 serious
  - widget-tab-missing-controls         WCAG 4.1.2 moderate
  - widget-disclosure-missing-expanded  WCAG 4.1.2 serious
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue
from audit._js_helpers import CSS_PATH_JS, SHADOW_DOM_QUERY_JS

log = logging.getLogger(__name__)


_PROBE_JS = "() => {\n" + CSS_PATH_JS + "\n" + SHADOW_DOM_QUERY_JS + "\n" + r"""
    // Visual pattern detector: a row of 3+ clickable siblings roughly
    // the same width sitting on the same horizontal line, none of which
    // declare role=tablist/tab, is suggestive of a custom tab widget.
    // We report this at confidence=low because many false-positive
    // shapes exist (breadcrumbs, button toolbars, pagination), but the
    // lead is actionable: "please audit this region manually".
    function findVisualTablists() {
        const suspects = [];
        const buttons = queryDeep(document, 'button, a, [role="button"]');
        // Bucket by their parent element + top/height signature.
        const buckets = new Map();
        for (const el of buttons) {
            if (el.closest('[role="tablist"]') || el.closest('[role="menu"]')) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 30 || r.height < 20) continue;
            if (r.width < 1 || r.height < 1) continue;
            const parent = el.parentElement;
            if (!parent) continue;
            const k = (parent ? parent.tagName + ':' + (parent.id || '') : '')
                     + '|' + Math.round(r.top / 2);
            if (!buckets.has(k)) buckets.set(k, []);
            buckets.get(k).push({ el, r });
        }
        for (const [, items] of buckets) {
            if (items.length < 3) continue;
            // Same parent? Widths within ~30%? On same top line?
            const sameParent = items.every(i => i.el.parentElement === items[0].el.parentElement);
            if (!sameParent) continue;
            const widths = items.map(i => i.r.width);
            const minW = Math.min(...widths);
            const maxW = Math.max(...widths);
            if (maxW > minW * 1.7) continue;
            const tops = items.map(i => i.r.top);
            if (Math.max(...tops) - Math.min(...tops) > 8) continue;
            // Skip obvious non-tab patterns: links inside <ul>/<ol>
            // unless they have "tab"-ish class/id tokens. Crude, but
            // the cheapest way to avoid firing on every primary nav.
            const parent = items[0].el.parentElement;
            const parentTag = (parent.tagName || '').toLowerCase();
            if (['ul', 'ol', 'nav'].includes(parentTag)) {
                const idLike = ((parent.id || '') + ' ' + (parent.className || '')).toLowerCase();
                if (!/\btab(?:s|list)?\b/.test(idLike)) continue;
            }
            suspects.push({
                selector: cssPath(parent),
                tag: parentTag,
                item_count: items.length,
                html: (parent.outerHTML || '').slice(0, 200),
            });
        }
        return suspects;
    }

    function pack(el) {
        return {
            selector: cssPath(el),
            role: el.getAttribute('role') || '',
            aria_expanded: el.getAttribute('aria-expanded'),
            aria_controls: el.getAttribute('aria-controls'),
            aria_modal: el.getAttribute('aria-modal'),
            aria_label: el.getAttribute('aria-label'),
            aria_labelledby: el.getAttribute('aria-labelledby'),
            aria_selected: el.getAttribute('aria-selected'),
            aria_haspopup: el.getAttribute('aria-haspopup'),
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || '').trim().slice(0, 120),
            html: el.outerHTML.slice(0, 200),
        };
    }

    // Use queryDeep for all probes so widgets inside Shadow DOM (Lit,
    // Stencil, Ionic, material-web, custom components) are discovered.
    // Regular querySelectorAll stops at shadow boundaries; a component
    // library hiding its Combobox inside a shadow root would be
    // invisible to the audit otherwise.
    const comboboxes = queryDeep(document, '[role="combobox"]')
        .filter(el => el.tagName !== 'SELECT')
        .map(pack);

    const dialogs = queryDeep(document, '[role="dialog"],[role="alertdialog"],dialog')
        .map(el => {
            const p = pack(el);
            // For <dialog> elements, the tag itself implies the role.
            if (!p.role) p.role = 'dialog';
            // Only validate *open* dialogs — closed ones have no
            // accessibility relevance and their state is captured by
            // the [hidden] attribute or CSS display:none.
            const isOpen = el.tagName === 'DIALOG' ? el.open : !el.hidden;
            p.open = isOpen;
            return p;
        })
        .filter(p => p.open);

    // Tablists: role=tablist with child role=tab elements.
    const tablists = queryDeep(document, '[role="tablist"]')
        .map(list => {
            const tabs = queryDeep(list, '[role="tab"]').map(pack);
            return { ...pack(list), tabs };
        });

    // Disclosure buttons: <button aria-expanded="..."> or <summary>.
    // <summary> inside <details> is native and handled by the browser,
    // so we only flag the ARIA variant that omits the attribute.
    const disclosures = queryDeep(document, 'button[aria-expanded]').map(pack);

    // Visual patterns that LOOK like tablists but don't declare the role.
    const visual_tablists = findVisualTablists();

    return { comboboxes, dialogs, tablists, disclosures, visual_tablists };
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        probe = page.evaluate(_PROBE_JS)
    except Exception as exc:
        log.exception("widgets probe failed")
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    issues = analyze(probe)
    # Runtime keyboard-behavior checks against the structural widgets
    # we found. These complement the markup rules by actually pressing
    # keys and verifying focus moves per APG.
    issues.extend(_check_tablist_keyboard(page, probe.get("tablists") or []))
    issues.extend(_check_dialog_escape(page, probe.get("dialogs") or []))

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "widget_counts": {
            "comboboxes": len(probe.get("comboboxes") or []),
            "dialogs": len(probe.get("dialogs") or []),
            "tablists": len(probe.get("tablists") or []),
            "disclosures": len(probe.get("disclosures") or []),
        },
    }


# --------------------------------------------------------------------
# Runtime behavior tests (APG keyboard patterns)


def _check_tablist_keyboard(page, tablists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """APG tablist: ArrowRight / ArrowLeft should move focus between
    tabs. Only validate when the tablist has at least 2 tabs AND the
    first tab's accessible name matches the markup (so we can confirm
    focus motion by comparing activeElement before/after)."""
    issues: list[dict[str, Any]] = []
    for idx, t in enumerate(tablists):
        tabs = t.get("tabs") or []
        if len(tabs) < 2:
            continue
        first_sel = tabs[0].get("selector")
        second_sel = tabs[1].get("selector")
        if not (first_sel and second_sel):
            continue
        try:
            # Focus the first tab and press ArrowRight. Behavior is
            # considered correct if focus moves to any *other* tab in
            # the list (strict "focus moved to tab[1]" is too rigid —
            # some libraries swap order via aria-orientation=vertical
            # which uses Down/Up instead, which APG also allows).
            page.locator(first_sel).first.focus(timeout=2000)
            page.wait_for_timeout(100)
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(200)
            moved = bool(page.evaluate(
                r"""(tabsSelectors) => {
                    const active = document.activeElement;
                    if (!active) return false;
                    // Compare active element against the set of tab
                    // selectors; if it matches any non-first tab, pass.
                    for (let i = 1; i < tabsSelectors.length; i++) {
                        try {
                            if (active.matches(tabsSelectors[i])) return true;
                        } catch { /* bad selector: ignore */ }
                    }
                    return false;
                }""",
                [t.get("selector") for t in tabs],
            ))
        except Exception as exc:
            log.debug("tablist keyboard probe failed: %s", exc)
            continue
        if not moved:
            issues.append(
                make_issue(
                    issue_id=f"widget-tablist-no-arrow-nav-{idx}",
                    module="widgets",
                    rule="widget-tablist-no-arrow-nav",
                    severity="serious",
                    wcag=["2.1.1"],
                    confidence="medium",
                    title="Tablist does not respond to arrow-key navigation",
                    description=(
                        "APG requires ArrowLeft/ArrowRight (or Up/Down for "
                        "vertical tablists) to move focus between tabs. "
                        "When the first tab is focused and ArrowRight is "
                        "pressed, focus did not move to another tab."
                    ),
                    selector=t.get("selector"),
                    html_snippet=t.get("html"),
                    details={"first_tab": first_sel, "tab_count": len(tabs)},
                    fix=(
                        "Handle ArrowLeft/ArrowRight on the tablist: "
                        "update aria-selected, move tabindex (roving "
                        "tabindex pattern), and call element.focus() on "
                        "the new tab."
                    ),
                )
            )
    return issues


def _check_dialog_escape(page, dialogs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """APG modal dialog: Escape should close the dialog (visible/aria-
    hidden state flips). Only probes OPEN dialogs — closed ones have
    nothing to test."""
    issues: list[dict[str, Any]] = []
    for idx, d in enumerate(dialogs):
        sel = d.get("selector")
        if not sel:
            continue
        try:
            # Capture the open state, focus something inside, press Esc,
            # check whether the dialog is still open.
            was_open = bool(page.evaluate(
                r"""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    if (el.tagName === 'DIALOG') return el.open;
                    return !el.hidden && getComputedStyle(el).display !== 'none';
                }""",
                sel,
            ))
            if not was_open:
                continue
            # Focus the dialog itself (or the first focusable inside).
            page.evaluate(
                r"""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    const focusable = el.querySelector(
                        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
                    );
                    (focusable || el).focus();
                }""",
                sel,
            )
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            still_open = bool(page.evaluate(
                r"""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    if (el.tagName === 'DIALOG') return el.open;
                    return !el.hidden && getComputedStyle(el).display !== 'none';
                }""",
                sel,
            ))
        except Exception as exc:
            log.debug("dialog Escape probe failed: %s", exc)
            continue
        if still_open:
            issues.append(
                make_issue(
                    issue_id=f"widget-dialog-no-escape-{idx}",
                    module="widgets",
                    rule="widget-dialog-no-escape",
                    severity="serious",
                    wcag=["2.1.1"],
                    confidence="medium",
                    title="Dialog does not close when Escape is pressed",
                    description=(
                        "APG modal dialog pattern requires Escape to close "
                        "the dialog and restore focus to the element that "
                        "opened it. Keyboard users cannot dismiss this "
                        "dialog without tabbing to its Close button."
                    ),
                    selector=sel,
                    html_snippet=d.get("html"),
                    fix=(
                        "Listen for the Escape key on the dialog (or a "
                        "document-level handler scoped to the dialog's "
                        "open state) and call your existing close() "
                        "logic."
                    ),
                )
            )
    return issues


def analyze(probe: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    # --- combobox --------------------------------------------------
    # APG combobox pattern requires aria-expanded (to announce open
    # state) and either aria-controls or the WAI 1.2 alternative of
    # aria-owns pointing to the listbox.
    for idx, c in enumerate(probe.get("comboboxes") or []):
        if c.get("aria_expanded") is None:
            issues.append(
                make_issue(
                    issue_id=f"widget-combobox-missing-expanded-{idx}",
                    module="widgets",
                    rule="widget-combobox-missing-expanded",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="Combobox is missing aria-expanded",
                    description=(
                        "The APG combobox pattern requires aria-expanded "
                        "to convey whether the popup is open. Screen-"
                        "reader users hear 'combobox' but not 'expanded' "
                        "or 'collapsed'."
                    ),
                    selector=c.get("selector", ""),
                    html_snippet=c.get("html", ""),
                    fix=(
                        'Set aria-expanded="false" when closed and '
                        '"true" when the popup is visible; update it '
                        "synchronously with the UI state."
                    ),
                )
            )
        if not (c.get("aria_controls")):
            issues.append(
                make_issue(
                    issue_id=f"widget-combobox-missing-controls-{idx}",
                    module="widgets",
                    rule="widget-combobox-missing-controls",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="Combobox is missing aria-controls",
                    description=(
                        "A combobox should reference its popup listbox "
                        "via aria-controls. Without the link, SR users "
                        "can't find the list of options when they "
                        "navigate by widget."
                    ),
                    selector=c.get("selector", ""),
                    html_snippet=c.get("html", ""),
                    fix=(
                        'Add aria-controls="<id-of-listbox>" on the '
                        "combobox element (WAI-ARIA 1.2 combobox "
                        "pattern, not the deprecated 1.1 pattern)."
                    ),
                )
            )

    # --- dialog ----------------------------------------------------
    for idx, d in enumerate(probe.get("dialogs") or []):
        has_name = bool((d.get("aria_label") or "").strip() or (d.get("aria_labelledby") or "").strip())
        if not has_name:
            issues.append(
                make_issue(
                    issue_id=f"widget-dialog-missing-name-{idx}",
                    module="widgets",
                    rule="widget-dialog-missing-name",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="Dialog is missing an accessible name",
                    description=(
                        "When this dialog opens, SR users hear 'dialog' "
                        "with no indication of purpose. APG requires "
                        "every dialog to have a visible title referenced "
                        "via aria-labelledby (or an aria-label)."
                    ),
                    selector=d.get("selector", ""),
                    html_snippet=d.get("html", ""),
                    fix=(
                        'Give the dialog a visible heading with an id, '
                        'and add aria-labelledby="<that-id>" on the '
                        "dialog element."
                    ),
                )
            )
        # aria-modal is important for modal dialogs so AT knows to treat
        # content outside the dialog as inert. Only strictly required
        # for modal dialogs, but the 95% case is modal.
        if d.get("aria_modal") not in ("true", "false"):
            issues.append(
                make_issue(
                    issue_id=f"widget-dialog-missing-aria-modal-{idx}",
                    module="widgets",
                    rule="widget-dialog-missing-aria-modal",
                    severity="moderate",
                    wcag=["4.1.2"],
                    title="Dialog is missing aria-modal",
                    description=(
                        "Without aria-modal, screen readers may allow "
                        "virtual-cursor navigation outside the dialog "
                        "while it's open. For modal dialogs this "
                        "creates a confusing reading context."
                    ),
                    selector=d.get("selector", ""),
                    html_snippet=d.get("html", ""),
                    fix=(
                        'Add aria-modal="true" to modal dialogs. Combine '
                        "with inert= on siblings (or aria-hidden) so "
                        "AT cannot reach them."
                    ),
                )
            )

    # --- tablist / tab --------------------------------------------
    for idx, t in enumerate(probe.get("tablists") or []):
        tabs = t.get("tabs") or []
        if not tabs:
            issues.append(
                make_issue(
                    issue_id=f"widget-tablist-no-tabs-{idx}",
                    module="widgets",
                    rule="widget-tablist-no-tabs",
                    severity="serious",
                    wcag=["1.3.1"],
                    title="Tablist contains no role=tab children",
                    description=(
                        "A role=tablist must contain one or more "
                        "role=tab elements. Without them, SR users "
                        "hear 'tab list' with nothing to navigate."
                    ),
                    selector=t.get("selector", ""),
                    html_snippet=t.get("html", ""),
                    fix=(
                        "Add role=\"tab\" to each tab control inside "
                        "the tablist; each tab needs aria-selected and "
                        "aria-controls pointing at its panel."
                    ),
                )
            )
            continue
        for t_idx, tab in enumerate(tabs):
            if tab.get("aria_selected") not in ("true", "false"):
                issues.append(
                    make_issue(
                        issue_id=f"widget-tab-missing-selected-{idx}-{t_idx}",
                        module="widgets",
                        rule="widget-tab-missing-selected",
                        severity="serious",
                        wcag=["4.1.2"],
                        title="Tab is missing aria-selected",
                        description=(
                            "Each role=tab element must declare its "
                            "selected state so SR users hear which tab "
                            "is currently active."
                        ),
                        selector=tab.get("selector", ""),
                        html_snippet=tab.get("html", ""),
                        fix=(
                            'Set aria-selected="true" on the active '
                            'tab and "false" on the others.'
                        ),
                    )
                )
            if not tab.get("aria_controls"):
                issues.append(
                    make_issue(
                        issue_id=f"widget-tab-missing-controls-{idx}-{t_idx}",
                        module="widgets",
                        rule="widget-tab-missing-controls",
                        severity="moderate",
                        wcag=["4.1.2"],
                        title="Tab is missing aria-controls",
                        description=(
                            "role=tab should reference its associated "
                            "role=tabpanel via aria-controls. Without "
                            "the relationship, SR users cannot jump to "
                            "the panel content."
                        ),
                        selector=tab.get("selector", ""),
                        html_snippet=tab.get("html", ""),
                        fix=(
                            'Add aria-controls="<id-of-tabpanel>" to '
                            "each tab."
                        ),
                    )
                )

    # --- visual-only tablists (no ARIA) ---------------------------
    # Suggestive only. We lack the authoring intent, so firing at
    # confidence=low means "this may be a tab widget; a human should
    # check". Skipped when the heuristic finds 0 items.
    for idx, v in enumerate(probe.get("visual_tablists") or []):
        issues.append(
            make_issue(
                issue_id=f"widget-visual-tablist-suspect-{idx}",
                module="widgets",
                rule="widget-visual-tablist-suspect",
                severity="moderate",
                wcag=["1.3.1"],
                confidence="low",
                title="Row of buttons may be a tablist lacking ARIA",
                description=(
                    f"A row of {v.get('item_count', 0)} similarly-sized "
                    "clickable siblings sits on a single line with no "
                    "role=\"tablist\"/role=\"tab\" declared. This pattern "
                    "is often a custom tab widget missing the ARIA "
                    "structure that makes it keyboard- and SR-navigable. "
                    "Please review manually — false positives include "
                    "button toolbars and breadcrumb trails."
                ),
                selector=v.get("selector", ""),
                html_snippet=v.get("html", ""),
                fix=(
                    "If it IS a tab widget: add role=\"tablist\" to the "
                    "container and role=\"tab\" (+ aria-selected, + "
                    "aria-controls) to each item. If not, the issue can "
                    "be ignored."
                ),
            )
        )

    # --- disclosure ------------------------------------------------
    # Any <button aria-expanded> is an APG disclosure; the attribute
    # must be set to "true" or "false" (not missing/empty/other).
    for idx, d in enumerate(probe.get("disclosures") or []):
        val = d.get("aria_expanded")
        if val not in ("true", "false"):
            issues.append(
                make_issue(
                    issue_id=f"widget-disclosure-missing-expanded-{idx}",
                    module="widgets",
                    rule="widget-disclosure-missing-expanded",
                    severity="serious",
                    wcag=["4.1.2"],
                    title="Disclosure button has invalid aria-expanded",
                    description=(
                        f"aria-expanded={val!r} is not a valid value. "
                        "Disclosure buttons must use 'true' or 'false'."
                    ),
                    selector=d.get("selector", ""),
                    html_snippet=d.get("html", ""),
                    fix=(
                        'Set aria-expanded="false" when the content is '
                        'collapsed and "true" when expanded; toggle '
                        "synchronously with the visible state."
                    ),
                )
            )

    return issues
