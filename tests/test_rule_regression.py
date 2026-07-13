"""Rule regression harness — guards every rule against silent breakage.

Loads tests/fixtures/wcag/rule_regression.yaml. Each entry hands a
canned DOM snapshot to a module's `analyze(dom)` function and asserts:

  * every rule named in `must_fire` produced at least one issue;
  * no rule named in `must_not_fire` produced any issue.

This is the cheap-and-fast counterpart to the e2e suite. The e2e suite
proves the JS probe extracts the right shape from a live page; this
harness proves the Python-side rule logic produces the right output
given an extracted shape. Together they cover the rule pipeline
end-to-end without paying for a browser on every CI run.

When you add a new rule, add at least one positive and one negative
case here. A rule that's only checked on a "real" page risks
regressing into "fires on every page" or "fires on no page" without
anyone noticing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# Module → analyze function. Adding a new module: import it and add
# its analyze here. The harness skips entries whose `module` field
# isn't registered, which keeps fixture authoring decoupled from
# wiring (you can write a fixture before its module exists).
from audit import color_only as _color_only
from audit import live_regions as _live_regions
from audit import structure as _structure

_ANALYZERS = {
    "structure": _structure.analyze,
    "live_regions": _live_regions.analyze,
    "color_only": _color_only.analyze,
}


_FIXTURES_PATH = Path(__file__).parent / "fixtures" / "wcag" / "rule_regression.yaml"


def _load_fixtures() -> list[dict[str, Any]]:
    if not _FIXTURES_PATH.is_file():
        return []
    raw = yaml.safe_load(_FIXTURES_PATH.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise RuntimeError(
            f"{_FIXTURES_PATH}: top-level YAML must be a list of fixtures"
        )
    return raw


_FIXTURES = _load_fixtures()


@pytest.mark.parametrize(
    "fixture",
    _FIXTURES,
    ids=[f.get("id", "?") for f in _FIXTURES],
)
def test_rule_fires_or_not(fixture: dict[str, Any]):
    """Run the fixture's module on the canned DOM, assert rule fires."""
    module = fixture.get("module")
    dom = fixture.get("dom")
    must_fire = set(fixture.get("must_fire") or [])
    must_not_fire = set(fixture.get("must_not_fire") or [])

    analyzer = _ANALYZERS.get(module)
    if analyzer is None:
        pytest.skip(f"no analyzer registered for module {module!r}")

    issues = analyzer(dom)
    fired_rules = {iss.get("rule") for iss in issues if iss.get("rule")}

    missing = must_fire - fired_rules
    assert not missing, (
        f"fixture {fixture.get('id')!r}: expected rules did not fire: {missing}. "
        f"actually fired: {sorted(fired_rules)}"
    )

    spuriously = fired_rules & must_not_fire
    assert not spuriously, (
        f"fixture {fixture.get('id')!r}: rules fired that should not have: "
        f"{sorted(spuriously)}"
    )


def test_corpus_is_non_empty():
    """The harness without fixtures is a foot-gun — fail loud if empty."""
    assert _FIXTURES, (
        f"{_FIXTURES_PATH} is empty. Add at least one fixture so the "
        "regression harness has something to enforce."
    )


def test_every_module_has_at_least_one_negative_case():
    """For every module that has any `must_fire`, the corpus must have
    at least one fixture with empty `must_fire` — otherwise we can't
    detect regressions where a rule fires on every page."""
    modules_with_positive = {
        f["module"] for f in _FIXTURES
        if (f.get("must_fire") or []) and f.get("module")
    }
    modules_with_negative = {
        f["module"] for f in _FIXTURES
        if not (f.get("must_fire") or []) and f.get("module")
    }
    missing_negative = modules_with_positive - modules_with_negative
    assert not missing_negative, (
        f"these modules have positive fixtures but no negative case: "
        f"{missing_negative}. Add a fixture with `must_fire: []` so we "
        "catch over-firing regressions."
    )
