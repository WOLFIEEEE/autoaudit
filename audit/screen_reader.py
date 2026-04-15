"""Screen reader module.

Path A (this file): Cross-platform analysis of Chromium's accessibility tree,
the same tree that screen readers consume via UIA/AT-SPI/IAccessible2 before
applying their own verbosity rules. Catches the canonical "silent element"
class of issues without needing a real screen reader running.

Path B (deferred): Real NVDA speech capture on a Windows worker. The
NVDAController class below is the placeholder entry point; see the project
design doc for the add-on and worker architecture.

Rules implemented (Path A):
- sr-silent-interactive     WCAG 4.1.2  critical   interactive-role node has no accessible name
- sr-empty-heading          WCAG 1.3.1  serious    heading with no accessible name
- sr-duplicate-landmark     WCAG 1.3.1  moderate   two or more landmarks share role and have no distinguishing name
- sr-dialog-no-name         WCAG 4.1.2  serious    dialog / alertdialog with no accessible name

Caveat: Playwright's accessibility.snapshot() does not expose a `focusable`
flag. Rules that depend on focus context (e.g. detecting a <div tabindex=0>
with no semantic role) live in the keyboard module instead, where we walk
focus directly.

Chromium's tree also differs from real NVDA output in verbosity rules,
browse-mode reading order, and punctuation. Real NVDA testing (Path B)
stacks additional rules on top when available.
"""

from __future__ import annotations

import logging
import platform
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)


class NVDAUnavailableError(RuntimeError):
    """Raised when a Path B (real-NVDA) flow is requested but not available."""


INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "checkbox",
        "radio",
        "textbox",
        "searchbox",
        "combobox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "switch",
        "tab",
        "treeitem",
        "option",
        "slider",
        "spinbutton",
    }
)

# Landmark roles per ARIA spec (and HTML sectioning equivalents).
LANDMARK_ROLES = frozenset(
    {
        "banner",
        "complementary",
        "contentinfo",
        "form",
        "main",
        "navigation",
        "region",
        "search",
    }
)


def _walk(node: dict[str, Any]):
    """Depth-first iterator yielding every node in the a11y tree."""
    if not node:
        return
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for child in reversed(n.get("children") or []):
            stack.append(child)


def _selector_hint(node: dict[str, Any]) -> str:
    """Best-effort human-readable hint for a tree node.

    The a11y tree doesn't carry CSS selectors, so we build a role+name hint.
    When the real NVDA pass lands it can correlate these by role+name or
    by injecting unique test IDs.
    """
    role = node.get("role", "?")
    name = (node.get("name") or "").strip()
    if name:
        return f'{role}[name="{name[:60]}"]'
    return role


def analyze(tree: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not tree:
        return []

    issues: list[dict[str, Any]] = []
    landmarks_by_role: dict[str, list[dict[str, Any]]] = {}

    nodes = list(_walk(tree))

    for node in nodes:
        role = (node.get("role") or "").lower()
        name = (node.get("name") or "").strip()
        disabled = bool(node.get("disabled"))

        # 1. interactive-role node with no accessible name
        if role in INTERACTIVE_ROLES and not name and not disabled:
            issues.append(
                make_issue(
                    issue_id=f"sr-silent-interactive-{_selector_hint(node)}",
                    module="screen_reader",
                    rule="sr-silent-interactive",
                    severity="critical",
                    wcag=["4.1.2"],
                    title=f'<{role}> has no accessible name',
                    description=(
                        "Chromium's accessibility tree exposes this element as a "
                        f"{role} but with no name. Screen readers will announce the "
                        "role alone (e.g. 'button') with nothing to identify it."
                    ),
                    selector=_selector_hint(node),
                    details={"role": role, "tree_name": name},
                    fix=(
                        "Add visible text, aria-label, aria-labelledby, or (for "
                        "inputs) a <label for> association."
                    ),
                )
            )

        # 3. empty heading (numbered in the rule list above)
        if role == "heading" and not name:
            issues.append(
                make_issue(
                    issue_id=f"sr-empty-heading-{_selector_hint(node)}",
                    module="screen_reader",
                    rule="sr-empty-heading",
                    severity="serious",
                    wcag=["1.3.1"],
                    title=f'Heading level {node.get("level","?")} has no text',
                    description=(
                        "Screen-reader users navigate by heading; empty headings appear "
                        "in that list as blank entries and break the document outline."
                    ),
                    selector=_selector_hint(node),
                    details={"level": node.get("level")},
                    fix="Remove the empty heading or add descriptive text content.",
                )
            )

        # 4. dialog with no accessible name
        if role in ("dialog", "alertdialog") and not name:
            issues.append(
                make_issue(
                    issue_id=f"sr-dialog-no-name-{_selector_hint(node)}",
                    module="screen_reader",
                    rule="sr-dialog-no-name",
                    severity="serious",
                    wcag=["4.1.2"],
                    title=f"<{role}> has no accessible name",
                    description=(
                        f"When the {role} opens, screen readers announce '{role}' with "
                        "no indication of what it is for."
                    ),
                    selector=_selector_hint(node),
                    details={"role": role},
                    fix="Add aria-label or aria-labelledby pointing to the dialog title.",
                )
            )

        # Collect landmarks for duplicate detection.
        if role in LANDMARK_ROLES:
            landmarks_by_role.setdefault(role, []).append(node)

    # 5. duplicate landmarks with no distinguishing names
    for role, lms in landmarks_by_role.items():
        if len(lms) < 2:
            continue
        names = [(n.get("name") or "").strip() for n in lms]
        # All empty or any two sharing the same name.
        duplicates_by_name: dict[str, list[dict[str, Any]]] = {}
        for lm, nm in zip(lms, names):
            duplicates_by_name.setdefault(nm, []).append(lm)
        for nm, group in duplicates_by_name.items():
            if len(group) < 2:
                continue
            # Report the second-plus occurrences (the first one is "the canonical").
            for dup_idx, lm in enumerate(group[1:], start=1):
                issues.append(
                    make_issue(
                        issue_id=f"sr-duplicate-landmark-{role}-{nm or '_unnamed'}-{dup_idx}",
                        module="screen_reader",
                        rule="sr-duplicate-landmark",
                        severity="moderate",
                        wcag=["1.3.1"],
                        title=(
                            f'Multiple <{role}> landmarks share '
                            + (f'name "{nm}"' if nm else "no accessible name")
                        ),
                        description=(
                            "Screen-reader users navigate landmarks via a list. Two "
                            f"{role} landmarks with the same (or empty) name are "
                            "indistinguishable in that list."
                        ),
                        selector=_selector_hint(lm),
                        details={"role": role, "shared_name": nm, "count": len(group)},
                        fix=(
                            f'Give each <{role}> a distinct aria-label '
                            '(e.g. aria-label="Primary" vs "Footer").'
                        ),
                    )
                )

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Snapshot the Chromium a11y tree and run the Path A analyzer."""
    start = time.time()
    try:
        # interesting_only=False gives us every node, including generic ones
        # that normally get filtered out — we *want* those, because
        # "focusable generic" is one of our rules.
        tree = page.accessibility.snapshot(interesting_only=False)
    except Exception as exc:
        log.exception("accessibility snapshot failed")
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    issues = analyze(tree)

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "mode": "a11y-tree",
        "tree_nodes": sum(1 for _ in _walk(tree or {})),
        "note": (
            "Chromium a11y-tree analysis. Real NVDA speech capture (Path B) "
            "would stack additional rules when a Windows worker runs this job."
        ),
    }


# --------------------------------------------------------------------------
# Path B entry points — real NVDA on a Windows worker. Not implemented yet.
# --------------------------------------------------------------------------


class NVDAController:
    """Placeholder for Path B real-NVDA flow. Not implemented on non-Windows."""

    def ensure_running(self) -> None:
        if platform.system() != "Windows":
            raise NVDAUnavailableError(
                "NVDA is only available on Windows. Path A a11y-tree analysis "
                "runs regardless; see audit.screen_reader.run."
            )
        raise NotImplementedError("NVDA controller not yet implemented")

    def start_capture(self) -> None:
        raise NotImplementedError

    def stop_capture(self) -> None:
        raise NotImplementedError

    def analyze_results(self, tab_stops: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "ran": False,
            "stub": True,
            "issues": [],
            "tab_stops": len(tab_stops),
            "nvda_transcript": [],
        }

    def run_browse_mode(self, page) -> dict[str, Any]:  # noqa: ARG002
        return {"ran": False, "stub": True, "transcript": []}
