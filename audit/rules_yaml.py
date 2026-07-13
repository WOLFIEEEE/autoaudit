"""YAML-based rule loader for non-Python rule authors.

Lets product / QA / accessibility teams contribute simple DOM-pattern
rules without touching Python. Rule shape:

    - id: my-custom-rule
      title: "Login form is missing autocomplete"
      description: >
        Long-form explanation rendered into the issue card.
      severity: serious          # critical | serious | moderate | minor
      wcag: ["1.3.5"]
      confidence: high           # high | medium | low (default high)
      # One of these selectors fires the rule. `match` is required;
      # `require` (optional) adds a positive predicate that must hold
      # alongside the match for the rule to fire.
      match: "form input[type=email]"
      require_attribute: "autocomplete"         # must be present
      require_attribute_value: null              # if set, must equal
      forbid_attribute: null                     # if set, must be absent
      fix: "Add autocomplete=\"email\" to the input."

Semantics:
  - match           (required) CSS selector evaluated via document.querySelectorAll
  - require_attribute       element must have this attribute
  - require_attribute_value if require_attribute is set, the value must equal this
  - forbid_attribute        element must NOT have this attribute

Rules fire once PER MATCHED ELEMENT. The loader translates the YAML
into a JS probe and emits standard make_issue() dicts — no new
runtime machinery.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from audit._issue import make_issue
from audit._js_helpers import CSS_PATH_JS

log = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("id", "title", "severity", "wcag", "match")
_ALLOWED_SEVERITIES = {"critical", "serious", "moderate", "minor"}
_ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def load_rules(path: str | Path) -> list[dict[str, Any]]:
    """Parse a YAML rules file; return a list of validated rule dicts.

    Raises ValueError on any rule with an unsupported severity or
    missing required field — fail loud so typos surface at load time,
    not at rule-fire time.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to load YAML rules; `pip install pyyaml`"
        ) from exc

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"rules file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ValueError(f"{p}: top-level YAML must be a list of rules")

    rules: list[dict[str, Any]] = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ValueError(f"{p}: rule #{i} is not a mapping")
        missing = [f for f in _REQUIRED_FIELDS if f not in r]
        if missing:
            raise ValueError(f"{p}: rule #{i} missing fields: {missing}")
        if r["severity"] not in _ALLOWED_SEVERITIES:
            raise ValueError(
                f"{p}: rule {r['id']}: severity must be one of "
                f"{sorted(_ALLOWED_SEVERITIES)}"
            )
        conf = r.get("confidence", "high")
        if conf not in _ALLOWED_CONFIDENCE:
            raise ValueError(
                f"{p}: rule {r['id']}: confidence must be one of "
                f"{sorted(_ALLOWED_CONFIDENCE)}"
            )
        rules.append(r)
    return rules


def run(page, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate every rule against the live page; return issue dicts."""
    if not rules:
        return []
    probe_js = "(rules) => {\n" + CSS_PATH_JS + "\n" + r"""
        const out = [];
        for (const rule of rules) {
            let nodes;
            try {
                nodes = document.querySelectorAll(rule.match);
            } catch (e) {
                out.push({__error: true, rule_id: rule.id, error: String(e)});
                continue;
            }
            for (const el of nodes) {
                // Apply require / forbid predicates. If require_attribute
                // is set, it must be present (optionally with a value).
                if (rule.require_attribute) {
                    if (!el.hasAttribute(rule.require_attribute)) continue;
                    if (rule.require_attribute_value != null) {
                        if (el.getAttribute(rule.require_attribute) !== rule.require_attribute_value) continue;
                    }
                }
                if (rule.forbid_attribute && el.hasAttribute(rule.forbid_attribute)) {
                    continue;
                }
                out.push({
                    rule_id: rule.id,
                    selector: cssPath(el),
                    html: el.outerHTML.slice(0, 200),
                });
            }
        }
        return out;
    }
    """
    try:
        results = page.evaluate(probe_js, rules)
    except Exception:
        log.exception("YAML rules probe failed")
        return []

    # Build a fast lookup so we can attach rule metadata to each hit.
    by_id = {r["id"]: r for r in rules}
    issues: list[dict[str, Any]] = []
    for hit in results:
        if hit.get("__error"):
            log.warning(
                "YAML rule %s failed at runtime: %s",
                hit.get("rule_id"), hit.get("error"),
            )
            continue
        r = by_id.get(hit["rule_id"])
        if r is None:
            continue
        issues.append(
            make_issue(
                issue_id=f"{r['id']}-{hash(hit['selector']) & 0xFFFFFF:x}",
                module="yaml_rules",
                rule=r["id"],
                severity=r["severity"],
                wcag=list(r["wcag"] or []),
                confidence=r.get("confidence", "high"),
                title=r["title"],
                description=r.get("description", ""),
                selector=hit.get("selector", ""),
                html_snippet=hit.get("html", ""),
                fix=r.get("fix", ""),
            )
        )
    return issues
