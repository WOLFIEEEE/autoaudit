"""Generate docs/rules.md — the public catalog of every audit rule.

We do NOT maintain a hand-curated rule catalog: rules drift, the
catalog rots, and downstream consumers (procurement teams, customer
auditors, integrators) keep finding undocumented rules in real
reports. Instead, this script statically scans every audit module
for `make_issue(...)` calls, extracts the rule metadata at literal
arguments (id, severity, wcag, title, description, fix), and emits a
single Markdown reference grouped by module.

Limits:
  * Only literal kwargs are extracted. Rules built dynamically (e.g.
    rule=f"...{idx}") are flagged with a placeholder so you can spot
    them and decide whether they need documentation by hand.
  * String concatenation across multiple lines IS handled (Python's
    `ast` already concatenates adjacent string literals).
  * f-strings with literal-only parts are kept as their template form.

Usage:
    python scripts/gen_rules_reference.py            # writes docs/rules.md
    python scripts/gen_rules_reference.py --check    # exits 1 if stale
    python scripts/gen_rules_reference.py --stdout   # prints to stdout

Add a CI step that calls --check to keep docs/rules.md current.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = REPO_ROOT / "audit"
OUTPUT_PATH = REPO_ROOT / "docs" / "rules.md"

# Constants tracked across calls to make_issue. Anything marked as
# "dynamic" is shown to the reader so they can audit gaps.
_DYNAMIC_PLACEHOLDER = "<dynamically computed>"

# Fields we pull out of make_issue. Order matters for the table.
_INTERESTING_KWARGS = (
    "rule", "module", "severity", "wcag", "confidence",
    "title", "description", "fix",
)


def _literal_value(node: ast.expr) -> Any:
    """Best-effort literal extraction. Handles strings, numbers, lists
    of literals, and f-strings with no expressions. Anything else
    returns the dynamic-placeholder sentinel."""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass
    if isinstance(node, ast.JoinedStr):
        # f-string. Concatenate static parts; replace expression parts
        # with `{...}` so the reader sees the template skeleton.
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            else:
                out.append("{...}")
        return "".join(out)
    return _DYNAMIC_PLACEHOLDER


def _extract_make_issue_calls(path: Path) -> list[dict[str, Any]]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        print(f"warn: skipping {path} (syntax error: {exc})", file=sys.stderr)
        return []
    rules: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match `make_issue(...)`, `_issue.make_issue(...)`, etc.
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "make_issue":
            continue
        rec: dict[str, Any] = {"_source_file": path.relative_to(REPO_ROOT).as_posix(),
                               "_lineno": node.lineno}
        for kw in node.keywords:
            if kw.arg in _INTERESTING_KWARGS:
                rec[kw.arg] = _literal_value(kw.value)
        if "rule" in rec:  # only include calls that named a rule
            rules.append(rec)
    return rules


def _collect() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for path in sorted(AUDIT_DIR.glob("*.py")):
        if path.name.startswith("_") and path.name not in ("__init__.py",):
            continue
        rules.extend(_extract_make_issue_calls(path))
    # Deduplicate by rule id (a rule may be emitted from multiple
    # make_issue sites with the same id but different details). Keep
    # the first occurrence; merge wcag lists where helpful.
    seen: dict[str, dict[str, Any]] = {}
    for r in rules:
        rid = r.get("rule")
        if not isinstance(rid, str):
            continue
        if rid in seen:
            # Merge wcag if both are concrete lists.
            existing_wcag = seen[rid].get("wcag")
            new_wcag = r.get("wcag")
            if isinstance(existing_wcag, list) and isinstance(new_wcag, list):
                merged = sorted(set(existing_wcag) | set(new_wcag))
                seen[rid]["wcag"] = merged
            continue
        seen[rid] = r
    return list(seen.values())


def _render_markdown(rules: list[dict[str, Any]]) -> str:
    # Group by module.
    by_module: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        mod = r.get("module") or _module_from_path(r.get("_source_file", ""))
        by_module.setdefault(mod, []).append(r)

    lines: list[str] = [
        "# Audit rules reference",
        "",
        "_Auto-generated by `scripts/gen_rules_reference.py`. "
        "**Do not edit by hand** — re-run the script to refresh._",
        "",
        f"Total rules: **{len(rules)}**, across {len(by_module)} modules.",
        "",
        "| Rule ID | WCAG | Severity | Module |",
        "|---|---|---|---|",
    ]
    for r in sorted(rules, key=lambda r: r.get("rule") or ""):
        rid = r.get("rule") or "?"
        wcag = r.get("wcag")
        wcag_s = (
            ", ".join(wcag) if isinstance(wcag, list)
            else wcag if isinstance(wcag, str)
            else "—"
        ) or "—"
        sev = r.get("severity") or "—"
        mod = r.get("module") or _module_from_path(r.get("_source_file", ""))
        lines.append(f"| `{rid}` | {wcag_s} | {sev} | {mod} |")
    lines.append("")

    for module in sorted(by_module):
        lines.append(f"## `{module}`")
        lines.append("")
        for r in sorted(by_module[module], key=lambda r: r.get("rule") or ""):
            rid = r.get("rule") or "?"
            sev = r.get("severity") or "—"
            wcag = r.get("wcag")
            wcag_s = (
                ", ".join(wcag) if isinstance(wcag, list)
                else wcag if isinstance(wcag, str)
                else "—"
            )
            confidence = r.get("confidence") or "high"
            lines.extend([
                f"### `{rid}`",
                "",
                f"- **Severity:** {sev}",
                f"- **WCAG:** {wcag_s or '—'}",
                f"- **Confidence:** {confidence}",
                f"- **Source:** `{r.get('_source_file')}:{r.get('_lineno')}`",
                "",
            ])
            title = r.get("title") or ""
            if title and title != _DYNAMIC_PLACEHOLDER:
                lines.append(f"**Sample title:** {title}")
                lines.append("")
            description = r.get("description") or ""
            if description and description != _DYNAMIC_PLACEHOLDER:
                lines.append(description)
                lines.append("")
            fix = r.get("fix") or ""
            if fix and fix != _DYNAMIC_PLACEHOLDER:
                lines.append(f"**Fix:** {fix}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _module_from_path(path: str) -> str:
    """Fallback: derive module name from the source file path."""
    return Path(path).stem if path else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Exit 1 if docs/rules.md is stale. For CI.",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="Print to stdout instead of writing the file.",
    )
    args = parser.parse_args(argv)

    rules = _collect()
    rendered = _render_markdown(rules)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        existing = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.is_file() else ""
        if existing != rendered:
            print(
                "docs/rules.md is stale; re-run "
                "`python scripts/gen_rules_reference.py`",
                file=sys.stderr,
            )
            return 1
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {len(rules)} rules to {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
