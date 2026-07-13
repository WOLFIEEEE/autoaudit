"""Dynamic-state testing: run declared interactions and assert SR-
visible side effects.

Most real a11y bugs live in state transitions (opening a modal,
validating a form, toggling an accordion) — not initial render. This
module runs a small set of caller-declared interactions and emits
issues when the expected post-conditions aren't satisfied.

The DSL is intentionally narrow — three trigger actions (click,
Enter, Space, Escape) and three assertion kinds (focus_moves_to,
attribute_equals, live_region_fires). Anything broader would require
a general-purpose script runner, which is out of scope.

Rules emitted:
- `dynamic-focus-not-moved`      (WCAG 2.4.3 / A)
- `dynamic-attribute-not-set`    (WCAG 4.1.2 / A)
- `dynamic-live-region-silent`   (WCAG 4.1.3 / AA)
- `dynamic-trigger-not-found`    (config error — selector didn't match)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute every Interaction in `options["interactions"]` against
    the already-loaded `page` and return {ran, issues, ...}.

    Every interaction runs in isolation — one failure doesn't skip
    the rest. We don't try to reset page state between them; callers
    that need a clean slate should re-navigate between probes or
    declare a "reset" interaction of their own.
    """
    opts = options or {}
    interactions = opts.get("interactions") or []
    start = time.time()

    issues: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []

    for spec in interactions:
        # `spec` may arrive as a Pydantic model or a plain dict,
        # depending on whether we were called from the FastAPI layer
        # or from a test. Coerce to dict so the rest is uniform.
        if hasattr(spec, "model_dump"):
            spec = spec.model_dump()
        try:
            result = _run_one(page, spec)
        except Exception as exc:  # defensive — one bad probe can't sink the module
            log.exception("interaction %r raised", spec.get("name"))
            result = {
                "name": spec.get("name", "?"),
                "error": f"{type(exc).__name__}: {exc}",
                "issues": [],
            }
        executed.append({
            "name": result.get("name"),
            "passed": not result.get("issues"),
            "error": result.get("error"),
        })
        issues.extend(result.get("issues") or [])

    return {
        "ran": True,
        "issues": issues,
        "interactions_executed": executed,
        "duration_seconds": round(time.time() - start, 3),
    }


def _run_one(page, spec: dict[str, Any]) -> dict[str, Any]:
    name = spec.get("name") or spec.get("trigger_selector", "?")
    trigger_sel = spec["trigger_selector"]
    action = spec.get("trigger_action", "click")
    settle_ms = int(spec.get("settle_ms", 300))
    expect = spec.get("expect") or {}

    issues: list[dict[str, Any]] = []

    # Capture a "before" snapshot of any live region we're watching,
    # so we can compare post-trigger. `page.locator(...).count()==0`
    # gets reported as a trigger-not-found config error, not silent.
    live_sel = expect.get("live_region_fires")
    before_live_text = None
    if live_sel:
        try:
            loc = page.locator(live_sel)
            if loc.count() > 0:
                before_live_text = (loc.first.text_content() or "").strip()
        except Exception:
            before_live_text = None

    # Verify the trigger element exists before we poke it. Silent
    # misses would show up as focus-not-moved failures with no hint
    # that the test itself was misconfigured.
    try:
        trigger = page.locator(trigger_sel)
        if trigger.count() == 0:
            issues.append(
                make_issue(
                    issue_id=f"dynamic-trigger-not-found-{name}",
                    module="dynamic",
                    rule="dynamic-trigger-not-found",
                    severity="moderate",
                    wcag=[],
                    title=f"Trigger selector not found: {trigger_sel}",
                    description=(
                        "The declared interaction's trigger selector does "
                        "not match any element on the page. Interaction "
                        "was skipped."
                    ),
                    details={"name": name, "selector": trigger_sel},
                    fix="Check the selector in the audit's interactions list.",
                )
            )
            return {"name": name, "issues": issues}
    except Exception as exc:
        # Catch-all so a bad selector doesn't sink the run, but
        # log with class name so a config error (InvalidSelectorError)
        # is visibly distinct from a runtime failure (TimeoutError).
        log.warning(
            "interaction %r: trigger lookup raised %s: %s",
            name, type(exc).__name__, exc,
        )
        return {
            "name": name,
            "error": f"{type(exc).__name__}: {exc}",
            "issues": issues,
        }

    # Fire the trigger. For keyboard actions we focus the element
    # first so the key goes to the right target. Playwright's click()
    # defaults to simulating a real user click (hover + mouse-down/up)
    # so any keyboard-only handlers are explicitly NOT tested here
    # — those belong to the static keyboard walk.
    try:
        if action == "click":
            trigger.first.click(timeout=5000)
        else:
            trigger.first.focus(timeout=5000)
            key_map = {"enter": "Enter", "space": " ", "escape": "Escape"}
            page.keyboard.press(key_map.get(action, action))
    except Exception as exc:
        log.warning(
            "interaction %r: trigger %s raised %s: %s",
            name, action, type(exc).__name__, exc,
        )
        return {
            "name": name,
            "error": f"trigger {action!r} failed ({type(exc).__name__}): {exc}",
            "issues": issues,
        }

    # Settle delay for any async state changes (React re-renders,
    # XHR flows, aria-live announcement debounce).
    if settle_ms:
        page.wait_for_timeout(settle_ms)

    # --- focus_moves_to ------------------------------------------
    focus_sel = expect.get("focus_moves_to")
    if focus_sel:
        try:
            focused_matches = page.evaluate(
                """(sel) => {
                    const el = document.activeElement;
                    if (!el) return false;
                    try { return el.matches(sel); } catch { return false; }
                }""",
                focus_sel,
            )
        except Exception:
            focused_matches = False
        if not focused_matches:
            issues.append(
                make_issue(
                    issue_id=f"dynamic-focus-not-moved-{name}",
                    module="dynamic",
                    rule="dynamic-focus-not-moved",
                    severity="serious",
                    wcag=["2.4.3"],
                    title="Focus did not move to the expected element",
                    description=(
                        f"After the '{name}' interaction, focus should "
                        f"have landed on {focus_sel} but document."
                        "activeElement did not match. Keyboard and SR "
                        "users are left stranded on the previous stop."
                    ),
                    selector=focus_sel,
                    details={"interaction": name, "expected": focus_sel},
                    fix=(
                        "Programmatically focus the target after the "
                        "trigger completes (element.focus()), or ensure "
                        "the markup makes it the natural next tab stop."
                    ),
                )
            )

    # --- attribute_equals ----------------------------------------
    attr_expect = expect.get("attribute_equals") or {}
    if attr_expect:
        a_sel = attr_expect.get("selector")
        a_name = attr_expect.get("name")
        a_val = attr_expect.get("value")
        if a_sel and a_name:
            try:
                actual = page.locator(a_sel).first.get_attribute(a_name)
            except Exception:
                actual = None
            if actual != a_val:
                issues.append(
                    make_issue(
                        issue_id=f"dynamic-attribute-not-set-{name}-{a_name}",
                        module="dynamic",
                        rule="dynamic-attribute-not-set",
                        severity="serious",
                        wcag=["4.1.2"],
                        title=(
                            f"Expected {a_name}={a_val!r} after '{name}' "
                            f"interaction; got {actual!r}"
                        ),
                        description=(
                            f"The element at {a_sel} did not reflect its "
                            "new state via ARIA. Screen-reader users won't "
                            "hear the state change on the toggled control."
                        ),
                        selector=a_sel,
                        details={
                            "interaction": name,
                            "attribute": a_name,
                            "expected": a_val,
                            "actual": actual,
                        },
                        fix=(
                            "Set the aria-expanded / aria-pressed / "
                            "aria-checked attribute synchronously with "
                            "the visible state change."
                        ),
                    )
                )

    # --- live_region_fires ---------------------------------------
    #
    # Two distinct failure modes we report under the same rule:
    #   a) text content did not change at all (no side-effect)
    #   b) text changed, but the element lacks aria-live / role=status
    #      / role=alert — SR users still won't hear it.
    #
    # Both are SR-silent status updates from the user's perspective.
    if live_sel:
        after = None
        is_live = False
        try:
            loc = page.locator(live_sel)
            if loc.count() > 0:
                after = (loc.first.text_content() or "").strip()
                # Probe whether the element (or an ancestor) actually
                # acts as an aria-live region. Roles status/alert/log
                # imply polite/assertive/polite live regions respectively.
                is_live = bool(page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        let n = el;
                        while (n) {
                            const live = n.getAttribute && n.getAttribute('aria-live');
                            const role = n.getAttribute && n.getAttribute('role');
                            if (live && live !== 'off') return true;
                            if (role && ['status', 'alert', 'log'].includes(role)) return true;
                            n = n.parentElement;
                        }
                        return false;
                    }""",
                    live_sel,
                ))
        except Exception:
            after = None
            is_live = False

        text_changed = after is not None and after != (before_live_text or "")
        if not text_changed:
            issues.append(
                make_issue(
                    issue_id=f"dynamic-live-region-silent-{name}-nochange",
                    module="dynamic",
                    rule="dynamic-live-region-silent",
                    severity="serious",
                    wcag=["4.1.3"],
                    title=(
                        f"Live region did not update after '{name}' "
                        "interaction"
                    ),
                    description=(
                        "The element's text content did not change after "
                        "the trigger fired. Screen-reader users will not "
                        "hear this status update."
                    ),
                    selector=live_sel,
                    details={
                        "interaction": name,
                        "live_region": live_sel,
                        "text_before": (before_live_text or "")[:120],
                        "text_after": (after or "")[:120],
                    },
                    fix=(
                        "Ensure the trigger actually writes updated text "
                        "into the declared status element."
                    ),
                )
            )
        elif not is_live:
            issues.append(
                make_issue(
                    issue_id=f"dynamic-live-region-silent-{name}-notlive",
                    module="dynamic",
                    rule="dynamic-live-region-silent",
                    severity="serious",
                    wcag=["4.1.3"],
                    title=(
                        "Status text changed but the element is not an "
                        "aria-live region"
                    ),
                    description=(
                        "The text content updated after the trigger, but "
                        "the element lacks aria-live / role=status / "
                        "role=alert on itself or any ancestor. Screen "
                        "readers observe changes only to live regions, "
                        "so this status will not be announced."
                    ),
                    selector=live_sel,
                    details={
                        "interaction": name,
                        "live_region": live_sel,
                        "text_before": (before_live_text or "")[:120],
                        "text_after": (after or "")[:120],
                    },
                    fix=(
                        "Add role=\"status\" (polite) or role=\"alert\" "
                        "(assertive) to the status element, or wrap it "
                        "in a parent with aria-live=\"polite\". The "
                        "attribute must be present BEFORE the text "
                        "change — freshly-inserted live regions are not "
                        "announced."
                    ),
                )
            )

    # --- error_describes_field ------------------------------------
    #
    # Form error flows: after submitting an invalid form we expect the
    # error message element to be associated with the field it refers
    # to, via the field's aria-describedby pointing at the error's id.
    # Without that link, SR users hear "invalid entry" but not what's
    # wrong. WCAG 3.3.1 Error Identification (level A).
    err_assoc = expect.get("error_describes_field") or {}
    if err_assoc:
        err_sel = err_assoc.get("error_selector")
        field_sel = err_assoc.get("field_selector")
        if err_sel and field_sel:
            try:
                linked = bool(page.evaluate(
                    r"""({errSel, fieldSel}) => {
                        const err = document.querySelector(errSel);
                        const field = document.querySelector(fieldSel);
                        if (!err || !field) return false;
                        const errId = err.id;
                        if (!errId) return false;
                        const describedby = field.getAttribute('aria-describedby') || '';
                        const ids = describedby.split(/\s+/).filter(Boolean);
                        return ids.includes(errId);
                    }""",
                    {"errSel": err_sel, "fieldSel": field_sel},
                ))
            except Exception:
                linked = False
            if not linked:
                issues.append(
                    make_issue(
                        issue_id=f"dynamic-error-not-associated-{name}",
                        module="dynamic",
                        rule="dynamic-error-not-associated",
                        severity="serious",
                        wcag=["3.3.1"],
                        title=(
                            "Form error message is not programmatically "
                            "linked to its field"
                        ),
                        description=(
                            f"After the '{name}' interaction, the error "
                            f"message at {err_sel} is not referenced by "
                            f"the input at {field_sel} via aria-describedby. "
                            "Screen-reader users hear 'invalid entry' with "
                            "no explanation of what went wrong."
                        ),
                        selector=field_sel,
                        details={
                            "interaction": name,
                            "error_selector": err_sel,
                            "field_selector": field_sel,
                        },
                        fix=(
                            "Give the error message an id, then add "
                            "aria-describedby=\"<error-id>\" to the form "
                            "field. Set aria-invalid=\"true\" on the "
                            "field at the same time so the SR announces "
                            "both the state and the error text."
                        ),
                    )
                )

    # --- modal_focus_trap -----------------------------------------
    #
    # Pre-baked suite: when the trigger is expected to open a modal
    # dialog, run the canonical accessibility checks for it:
    #   1. activeElement is inside a [role=dialog] / [role=alertdialog]
    #   2. Tab cycles forward stay inside that ancestor
    #   3. Escape closes the dialog
    #
    # Covers WCAG 2.1.2 (No Keyboard Trap), 2.4.3 (Focus Order),
    # 4.1.2 (Name, Role, Value), and 2.1.1 (Keyboard).
    if expect.get("modal_focus_trap"):
        issues.extend(_check_modal_focus_trap(page, name))

    return {"name": name, "issues": issues}


# Maximum Tab presses inside a modal during the focus-trap suite. Set
# generously: a complex modal (form + footer) can have 15+ stops, so
# 30 covers two full cycles. We bail early if focus escapes — the cap
# is a circuit breaker for *successful* traps, not a tightness target.
_MODAL_TAB_BUDGET = 30


def _check_modal_focus_trap(page, interaction_name: str) -> list[dict[str, Any]]:
    """Run the modal-focus-trap suite. Returns a list of issues."""
    issues: list[dict[str, Any]] = []

    try:
        dialog_info = page.evaluate(
            r"""() => {
                const el = document.activeElement;
                if (!el) return {has_focus: false};
                let cur = el;
                while (cur && cur !== document.body) {
                    const role = (cur.getAttribute && cur.getAttribute('role')) || '';
                    const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
                    if (role === 'dialog' || role === 'alertdialog' || tag === 'dialog') {
                        const aria_label = cur.getAttribute('aria-label') || '';
                        const labelledby = cur.getAttribute('aria-labelledby') || '';
                        let label_text = aria_label;
                        if (!label_text && labelledby) {
                            label_text = labelledby.split(/\s+/).map(id => {
                                const ref = document.getElementById(id);
                                return ref ? (ref.textContent || '').trim() : '';
                            }).join(' ').trim();
                        }
                        return {
                            has_focus: true,
                            in_dialog: true,
                            tag,
                            role,
                            label: label_text,
                            dialog_id: cur.id || null,
                        };
                    }
                    cur = cur.parentElement;
                }
                return {has_focus: true, in_dialog: false};
            }"""
        )
    except Exception as exc:
        log.debug("modal probe failed: %s", exc)
        return issues

    if not dialog_info.get("has_focus"):
        issues.append(make_issue(
            issue_id=f"dynamic-modal-no-focus-{interaction_name}",
            module="dynamic",
            rule="dynamic-modal-no-focus",
            severity="serious",
            wcag=["2.4.3"],
            title=f"Modal opened by '{interaction_name}' did not move focus",
            description=(
                "After the trigger fired, no element has focus "
                "(activeElement is null or the body). A modal dialog "
                "must move focus into itself when it opens; otherwise "
                "keyboard users continue to operate the page underneath."
            ),
            details={"interaction": interaction_name},
            fix=(
                "When the modal opens, programmatically focus its "
                "first interactive element or its container "
                '(dialog.focus()).'
            ),
        ))
        return issues

    if not dialog_info.get("in_dialog"):
        issues.append(make_issue(
            issue_id=f"dynamic-modal-focus-outside-{interaction_name}",
            module="dynamic",
            rule="dynamic-modal-focus-outside",
            severity="serious",
            wcag=["2.4.3", "4.1.2"],
            title=(
                f"'{interaction_name}' opened a modal but focus is not "
                "inside any [role=dialog]"
            ),
            description=(
                "After the trigger fired, the focused element does "
                "not sit within a [role=dialog], [role=alertdialog], "
                "or <dialog> ancestor. Either the modal lacks a "
                "dialog role / element, or focus did not move into "
                "the dialog. WCAG 2.4.3 and 4.1.2 both fail."
            ),
            details={"interaction": interaction_name},
            fix=(
                "Wrap the modal in a <dialog> element (or a div with "
                "role=\"dialog\" and aria-modal=\"true\"), then focus "
                "an element inside it on open."
            ),
        ))
        return issues

    if not (dialog_info.get("label") or "").strip():
        issues.append(make_issue(
            issue_id=f"dynamic-modal-no-name-{interaction_name}",
            module="dynamic",
            rule="dynamic-modal-no-name",
            severity="serious",
            wcag=["4.1.2"],
            title=f"Modal opened by '{interaction_name}' has no accessible name",
            description=(
                "The dialog ancestor has neither aria-label nor "
                "aria-labelledby pointing to a non-empty title. Screen "
                "reader users land in an unnamed dialog and cannot "
                "tell what it is for."
            ),
            details={
                "interaction": interaction_name,
                "dialog_role": dialog_info.get("role"),
            },
            fix=(
                "Add aria-labelledby pointing to the dialog's heading, "
                "or aria-label with a short descriptive name."
            ),
        ))

    escape_idx: int | None = None
    for i in range(_MODAL_TAB_BUDGET):
        try:
            page.keyboard.press("Tab")
            page.wait_for_timeout(40)
            still_inside = page.evaluate(
                r"""() => {
                    const el = document.activeElement;
                    if (!el) return false;
                    let cur = el;
                    while (cur && cur !== document.body) {
                        const role = (cur.getAttribute && cur.getAttribute('role')) || '';
                        const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
                        if (role === 'dialog' || role === 'alertdialog' || tag === 'dialog') return true;
                        cur = cur.parentElement;
                    }
                    return false;
                }"""
            )
        except Exception:
            still_inside = True  # fail-open; don't false-positive on infra
        if not still_inside:
            escape_idx = i + 1
            break
    if escape_idx is not None:
        issues.append(make_issue(
            issue_id=f"dynamic-modal-not-trapped-{interaction_name}",
            module="dynamic",
            rule="dynamic-modal-not-trapped",
            severity="serious",
            wcag=["2.1.2"],
            title=(
                f"Modal '{interaction_name}' does not trap focus — Tab "
                f"escaped after {escape_idx} press(es)"
            ),
            description=(
                "While a modal dialog is open, Tab should cycle within "
                "the dialog rather than reaching the page underneath. "
                "Keyboard users escape into the inert background and "
                "lose context of what they were doing in the modal."
            ),
            details={
                "interaction": interaction_name,
                "tab_presses_to_escape": escape_idx,
            },
            fix=(
                "Implement a focus trap: capture Tab/Shift+Tab while "
                "the dialog is open and wrap focus from the last "
                "tabbable element to the first (and vice versa). The "
                "<dialog> element with showModal() does this natively."
            ),
        ))

    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        still_open = page.evaluate(
            r"""() => {
                const all = document.querySelectorAll('[role="dialog"], [role="alertdialog"], dialog');
                for (const d of all) {
                    const s = getComputedStyle(d);
                    if (s.display !== 'none' && s.visibility !== 'hidden') return true;
                }
                return false;
            }"""
        )
    except Exception:
        still_open = False
    if still_open:
        issues.append(make_issue(
            issue_id=f"dynamic-modal-escape-no-close-{interaction_name}",
            module="dynamic",
            rule="dynamic-modal-escape-no-close",
            severity="moderate",
            wcag=["2.1.1"],
            title=f"Modal '{interaction_name}' does not close on Escape",
            description=(
                "Pressing Escape did not close the dialog. Keyboard "
                "users expect Escape to dismiss modals; without it, "
                "they must locate the (often visually-only) close "
                "button."
            ),
            details={"interaction": interaction_name},
            fix=(
                "Listen for the Escape key while the modal is open "
                "and dismiss the dialog. The <dialog> element with "
                "showModal() handles this natively."
            ),
        ))
    return issues
