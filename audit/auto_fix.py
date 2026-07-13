"""Auto-fix patch generator for deterministic rules.

For a small set of rules whose remedy is mechanical, we can emit a
unified-diff hunk that — applied with `git apply` or `patch -p0` —
fixes the issue. This is different from the prose `fix_suggestion`
field on every issue: that's instruction; this is executable.

Eligibility criteria for a rule to ship an auto-fix:

  1. **Single-element edit.** The remedy touches exactly one DOM node.
  2. **No semantic ambiguity.** The fix has one obviously-right form
     (adding `lang="en"`, removing `aria-hidden`, adding `tabindex="-1"`).
  3. **Reversible.** Worst case: a stylistically-suboptimal fix the
     team cleans up by hand. Never something destructive.

Rules currently auto-fixable:

  - structure-html-lang        → add lang="en" to <html>
  - structure-iframe-no-title  → add title="<TODO: describe>" placeholder
  - aria-hidden-focusable      → remove aria-hidden="true"
  - skiplink-target-not-focusable → add tabindex="-1" to the target
  - keyboard-positive-tabindex → tabindex="N" → tabindex="0"

The patch is heuristic: it works against the captured `html_snippet`
and best-effort cssPath. Real-world repos that source-render their
HTML need to map the snippet back to the template file. That's why
the rule emits a CONTEXT-style patch (the snippet shows up as the
`-`/`+` block) rather than touching files directly.

`generate_patches(audit_result)` returns a list of patch hunks.
`scripts/apply_audit_fixes.py` (TODO: separate companion script) can
batch-apply them; this module just produces the data.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def _patch(rule: str, before: str, after: str, file_hint: str = "") -> dict[str, Any]:
    """Build a unified-diff-shaped patch hunk."""
    return {
        "rule": rule,
        "file_hint": file_hint or "<source-file-containing-this-snippet>",
        "before": before,
        "after": after,
        "diff": _unified(before, after, file_hint),
    }


def _unified(before: str, after: str, file_hint: str) -> str:
    """Produce a 2-line unified-diff hunk approximation.

    We don't run difflib here because the snippets are short and the
    eyeball-readable `- old` / `+ new` form is what reviewers expect.
    A full unified diff with file paths would be misleading — we
    don't actually know the file the snippet came from.
    """
    file_label = file_hint or "(unknown source file containing the snippet)"
    return (
        f"--- {file_label}\n"
        f"+++ {file_label}\n"
        + "\n".join(f"- {line}" for line in before.splitlines())
        + "\n"
        + "\n".join(f"+ {line}" for line in after.splitlines())
        + "\n"
    )


def _add_attr(html_snippet: str, attr: str, value: str) -> str | None:
    """Insert `attr="value"` into the opening tag. Returns None when
    we can't safely identify the opening tag."""
    m = re.match(r"^(<[a-zA-Z][a-zA-Z0-9-]*)(\s|>|/>)", html_snippet)
    if not m:
        return None
    tag_name_end = m.end(1)
    return (
        html_snippet[:tag_name_end]
        + f' {attr}="{value}"'
        + html_snippet[tag_name_end:]
    )


def _remove_attr(html_snippet: str, attr: str) -> str | None:
    """Remove `attr="..."` from the opening tag. Returns None on no match."""
    pat = re.compile(rf'\s+{re.escape(attr)}\s*=\s*"[^"]*"')
    new = pat.sub("", html_snippet, count=1)
    if new == html_snippet:
        # Try the unquoted form.
        pat2 = re.compile(rf"\s+{re.escape(attr)}\s*=\s*[^\s>]+")
        new = pat2.sub("", html_snippet, count=1)
    return new if new != html_snippet else None


def _replace_attr(html_snippet: str, attr: str, value: str) -> str | None:
    """Replace `attr="..."` with `attr="value"`. Returns None on no match."""
    pat = re.compile(rf'(\s+{re.escape(attr)}\s*=\s*)("[^"]*"|\'[^\']*\'|[^\s>]+)')
    if not pat.search(html_snippet):
        return None
    return pat.sub(rf'\1"{value}"', html_snippet, count=1)


def _patch_for_issue(issue: dict[str, Any]) -> dict[str, Any] | None:
    rule = issue.get("rule") or ""
    snippet = (issue.get("element") or {}).get("html_snippet") or ""
    if not snippet:
        return None
    selector = (issue.get("element") or {}).get("selector", "")

    if rule == "structure-html-lang":
        # Synthesise the snippet from scratch — the issue points at
        # <html> which usually doesn't ship with html_snippet anyway.
        before = "<html>"
        after = '<html lang="en">'
        return _patch(rule, before, after, file_hint=selector or "html")

    if rule == "structure-iframe-no-title":
        new = _add_attr(snippet, "title", "TODO: describe the embedded content")
        if not new:
            return None
        return _patch(rule, snippet, new, file_hint=selector)

    if rule == "aria-hidden-focusable":
        new = _remove_attr(snippet, "aria-hidden")
        if not new:
            return None
        return _patch(rule, snippet, new, file_hint=selector)

    if rule == "skiplink-target-not-focusable":
        new = _add_attr(snippet, "tabindex", "-1")
        if not new:
            return None
        return _patch(rule, snippet, new, file_hint=selector)

    if rule == "keyboard-positive-tabindex":
        new = _replace_attr(snippet, "tabindex", "0")
        if not new:
            return None
        return _patch(rule, snippet, new, file_hint=selector)

    return None


def generate_patches(audit_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of auto-fix patches for every applicable issue.

    Each entry includes the issue's id and rule for traceability so a
    UI can show "fix the keyboard-positive-tabindex finding on
    #my-button-3" alongside the patch.
    """
    out: list[dict[str, Any]] = []
    for issue in audit_result.get("issues") or []:
        patch = _patch_for_issue(issue)
        if not patch:
            continue
        patch["issue_id"] = issue.get("id")
        patch["fingerprint"] = issue.get("fingerprint")
        out.append(patch)
    return out
