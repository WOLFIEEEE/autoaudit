"""Report and enforce benchmark-corpus coverage of the rule set.

Why this exists
---------------
A rule with no fixture has no precision number, and a rule with no
precision number cannot be improved or defended — it can only be
believed. At the time this landed, 13 of 132 rules had any measurement,
which is why a browse-mode bug that fired 20 false `serious` findings on
clean pages reached production unnoticed.

Two kinds of coverage, and they are not interchangeable:

- **Positive** — the rule appears in some fixture's `expected[]`. Proves
  the rule fires when it should. Gives recall.
- **Negative** — the rule appears in a clean fixture's `forbidden[]`.
  Proves the rule stays quiet when it should. Gives precision.

A rule needs both. Negative coverage is the cheaper of the two and
catches the more expensive class of bug, so the clean-page suite is
where to start.

Usage
-----
    python scripts/fixture_coverage.py                # report
    python scripts/fixture_coverage.py --check        # gate on the ratchet
    python scripts/fixture_coverage.py --generate-stubs   # scaffold the gaps
    python scripts/fixture_coverage.py --json         # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "benchmarks" / "corpus"

# Ratchet. Raise as fixtures land; never lower it. These are counts of
# distinct rules, not percentages, so the numbers stay legible as the
# rule set grows.
MIN_POSITIVE = 13
MIN_NEGATIVE = 109


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def all_rules() -> list[dict[str, Any]]:
    """Every rule the codebase can emit, from the make_issue call sites."""
    from scripts.gen_rules_reference import _collect

    return _collect()


def corpus_coverage() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return (positive, negative): rule id -> fixture names covering it."""
    positive: dict[str, list[str]] = {}
    negative: dict[str, list[str]] = {}
    if not CORPUS.is_dir():
        return positive, negative

    for fixture in sorted(CORPUS.iterdir()):
        truth_path = fixture / "ground_truth.yaml"
        if not truth_path.is_file():
            continue
        truth = _load_yaml(truth_path)
        for entry in truth.get("expected") or []:
            rule = entry.get("rule")
            if rule:
                positive.setdefault(rule, []).append(fixture.name)
        for entry in truth.get("forbidden") or []:
            rule = entry.get("rule")
            if rule:
                negative.setdefault(rule, []).append(fixture.name)
    return positive, negative


def analyse() -> dict[str, Any]:
    rules = all_rules()
    positive, negative = corpus_coverage()
    ids = [r["rule"] for r in rules]

    uncovered_pos = sorted(set(ids) - set(positive))
    uncovered_neg = sorted(set(ids) - set(negative))

    by_module: dict[str, dict[str, int]] = {}
    for r in rules:
        mod = r.get("module") or "?"
        slot = by_module.setdefault(mod, {"total": 0, "positive": 0, "negative": 0})
        slot["total"] += 1
        if r["rule"] in positive:
            slot["positive"] += 1
        if r["rule"] in negative:
            slot["negative"] += 1

    return {
        "rules_total": len(ids),
        "positive_covered": len(set(ids) & set(positive)),
        "negative_covered": len(set(ids) & set(negative)),
        "uncovered_positive": uncovered_pos,
        "uncovered_negative": uncovered_neg,
        "by_module": by_module,
        "fixtures": sorted(p.name for p in CORPUS.iterdir() if p.is_dir())
        if CORPUS.is_dir()
        else [],
    }


STUB_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fixture: {rule}</title>
</head>
<body>
  <main>
    <h1>Fixture: {rule}</h1>
    <!-- TODO: markup that makes `{rule}` fire exactly once.
         Keep it minimal: one violation, nothing incidental, so a
         failure here names one cause. -->
  </main>
</body>
</html>
"""

STUB_TRUTH = """notes: |
  TODO: describe what this fixture is and why the rule should fire.
  Minimal reproduction of `{rule}` ({wcag}).

  This stub is INCOMPLETE — `expected` is empty, so
  scripts/fixture_coverage.py still counts {rule} as uncovered.
expected:
  - rule: {rule}
    min_count: 1
forbidden: []
"""


def generate_stubs(rules_to_stub: list[dict[str, Any]], limit: int) -> list[str]:
    written: list[str] = []
    for r in rules_to_stub[:limit]:
        rule = r["rule"]
        name = rule.replace("-", "_")
        target = CORPUS / name
        if target.exists():
            continue
        target.mkdir(parents=True)
        wcag = r.get("wcag")
        wcag_s = ", ".join(wcag) if isinstance(wcag, list) else str(wcag or "?")
        (target / "page.html").write_text(STUB_HTML.format(rule=rule), encoding="utf-8")
        (target / "ground_truth.yaml").write_text(
            STUB_TRUTH.format(rule=rule, wcag=wcag_s), encoding="utf-8"
        )
        written.append(name)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit non-zero below the ratchet")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--generate-stubs", action="store_true", help="scaffold missing fixtures")
    ap.add_argument("--limit", type=int, default=10, help="max stubs per run (default 10)")
    args = ap.parse_args()

    data = analyse()

    if args.generate_stubs:
        rules = {r["rule"]: r for r in all_rules()}
        todo = [rules[r] for r in data["uncovered_positive"] if r in rules]
        written = generate_stubs(todo, args.limit)
        print(f"wrote {len(written)} stub fixture(s):")
        for name in written:
            print(f"  benchmarks/corpus/{name}/")
        print("\nFill in page.html for each, then re-run without --generate-stubs.")
        return 0

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    total = data["rules_total"]
    pos = data["positive_covered"]
    neg = data["negative_covered"]
    print("Benchmark corpus coverage")
    print("-" * 58)
    print(f"  rules shipped              {total}")
    print(f"  positive coverage (fires)  {pos:3d}  ({100*pos//total}%)  floor {MIN_POSITIVE}")
    print(f"  negative coverage (quiet)  {neg:3d}  ({100*neg//total}%)  floor {MIN_NEGATIVE}")
    print(f"  fixtures                   {len(data['fixtures'])}")
    print()
    print("  Least-covered modules:")
    worst = sorted(
        data["by_module"].items(),
        key=lambda kv: (kv[1]["positive"] - kv[1]["total"], -kv[1]["total"]),
    )[:8]
    for mod, s in worst:
        print(f"    {mod:22} {s['positive']:2d}/{s['total']:2d} positive   {s['negative']:2d}/{s['total']:2d} negative")

    if args.check:
        failures = []
        if pos < MIN_POSITIVE:
            failures.append(f"positive coverage {pos} < floor {MIN_POSITIVE}")
        if neg < MIN_NEGATIVE:
            failures.append(f"negative coverage {neg} < floor {MIN_NEGATIVE}")
        if failures:
            print("\nFAIL: " + "; ".join(failures))
            return 1
        print("\nOK: corpus coverage at or above the ratchet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
