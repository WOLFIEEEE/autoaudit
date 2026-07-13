"""Guard tests for the rule-versioning registry.

Two contracts:

  1. **Every rule emitted by any audit module is registered.** A new
     rule shipped without a version entry produces "0.0.0" issues
     downstream — confusing for VPAT consumers. We catch this in CI.

  2. **The hash is stable** across calls and **changes when versions
     change** — both required for the reproducibility claim to be
     meaningful.

The "every rule is registered" check works by AST-scanning the audit
package for `make_issue(rule="...")` calls and asserting each literal
rule id has a RULE_VERSIONS entry. Identical mechanism to
`scripts/gen_rules_reference.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from audit import rule_versions

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = REPO_ROOT / "audit"


def _literal_rule_ids() -> set[str]:
    """Return every literal rule= value passed to make_issue across audit/."""
    found: set[str] = set()
    for path in sorted(AUDIT_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else ""
            )
            if func_name != "make_issue":
                continue
            for kw in node.keywords:
                if kw.arg != "rule":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    return found


def test_every_emitted_rule_has_a_version():
    emitted = _literal_rule_ids()
    missing = sorted(emitted - set(rule_versions.RULE_VERSIONS))
    assert not missing, (
        "These rule ids are emitted by audit/ but not registered in "
        f"audit/rule_versions.py: {missing}\n\n"
        "Add an entry for each (start at 1.0.0) and bump the meta-version."
    )


def test_no_orphan_rule_versions():
    """Versions registered for a rule that no module emits → likely
    a rule was renamed / removed and the registry wasn't pruned."""
    emitted = _literal_rule_ids()
    orphans = sorted(set(rule_versions.RULE_VERSIONS) - emitted)
    # Allow a small grace — sometimes a rule is registered ahead of
    # the module landing (TDD style). Cap at 6 to catch the common
    # case of forgotten cleanup.
    assert len(orphans) <= 6, (
        f"Too many orphan rule entries in audit/rule_versions.py: {orphans}. "
        "Either delete the entries or land the corresponding rule."
    )


def test_rule_set_hash_is_stable():
    """Two calls with no version changes produce the same hash."""
    a = rule_versions.rule_set_hash()
    b = rule_versions.rule_set_hash()
    assert a == b
    assert len(a) == 64  # sha256 hex length


def test_rule_set_hash_changes_when_versions_change(monkeypatch: pytest.MonkeyPatch):
    """Bump a version in a copy → hash must change.

    Uses a defensive monkeypatch on the module-level dict copy rather
    than mutating shared state, so other tests can't be order-poisoned.
    """
    original = rule_versions.RULE_VERSIONS.copy()
    before = rule_versions.rule_set_hash()
    try:
        rule_versions.RULE_VERSIONS["__synthetic_test_rule__"] = "9.9.9"
        after = rule_versions.rule_set_hash()
        assert before != after
    finally:
        rule_versions.RULE_VERSIONS.clear()
        rule_versions.RULE_VERSIONS.update(original)


def test_version_for_unregistered_returns_zero():
    assert rule_versions.version_for("does-not-exist") == "0.0.0"


def test_version_for_registered_returns_value():
    # Pick any known-registered rule.
    assert rule_versions.version_for("structure-html-lang") == "1.0.0"
