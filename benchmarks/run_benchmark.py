"""Benchmark runner: measure rule precision / recall against the corpus.

For each fixture in benchmarks/corpus/:
  - load page.html through the full orchestrator
  - read ground_truth.yaml
  - score the findings against expected[] / forbidden[]
  - aggregate per-rule counts across fixtures

Output:
  - per-fixture pass/fail with reasons
  - per-rule precision (TP / (TP + FP)) + recall (TP / (TP + FN))

Precision / recall only make sense when a rule has non-zero ground-
truth signal across the corpus. Rules with no expected firings anywhere
get a "n/a" row — a hint to add targeted fixtures.

Exit code 0 only when every fixture passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORPUS = Path(__file__).resolve().parent / "corpus"


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # optional; we import here so the runner fails with a
                 # useful message if PyYAML is absent.
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _audit(url: str) -> dict[str, Any]:
    from audit.orchestrator import AuditOrchestrator
    return AuditOrchestrator(
        url=url,
        options={"skip_nvda": True, "headless": True},
    ).run()


def score_fixture(result: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """Score one fixture. Returns a per-fixture result dict.

    Categories:
      - passed (bool): expected and forbidden conditions all satisfied.
      - missed_expected: rules declared expected that didn't fire.
      - over_fired: expected rules that exceeded max_count.
      - false_positives: forbidden rules that fired anyway.
      - true_positives_per_rule: dict[rule -> count].
    """
    issues = result.get("issues") or []
    fired_counts: dict[str, int] = defaultdict(int)
    for i in issues:
        fired_counts[i.get("rule", "")] += 1

    missed: list[str] = []
    over: list[str] = []
    tps: dict[str, int] = {}
    for e in truth.get("expected") or []:
        rule = e.get("rule")
        got = fired_counts.get(rule, 0)
        min_count = e.get("min_count", 1)
        max_count = e.get("max_count")
        if got < min_count:
            missed.append(f"{rule} (expected >= {min_count}, got {got})")
        elif max_count is not None and got > max_count:
            over.append(f"{rule} (max {max_count}, got {got})")
        tps[rule] = min(got, max_count) if max_count else got

    fps: list[str] = []
    for f in truth.get("forbidden") or []:
        rule = f.get("rule")
        got = fired_counts.get(rule, 0)
        if got > 0:
            fps.append(f"{rule} ({got}x)")

    return {
        "passed": not (missed or over or fps),
        "missed_expected": missed,
        "over_fired": over,
        "false_positives": fps,
        "true_positives_per_rule": tps,
        "all_fired_rules": dict(fired_counts),
    }


def summarize(scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-rule P/R across all fixtures."""
    per_rule: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    for _name, s in scores.items():
        for rule, count in s["true_positives_per_rule"].items():
            per_rule[rule]["tp"] += count
        for fp in s["false_positives"]:
            # "rule (Nx)" -> rule
            rule = fp.split(" ", 1)[0]
            per_rule[rule]["fp"] += 1
        for miss in s["missed_expected"]:
            rule = miss.split(" ", 1)[0]
            per_rule[rule]["fn"] += 1

    table: list[dict[str, Any]] = []
    for rule in sorted(per_rule):
        tp = per_rule[rule]["tp"]
        fp = per_rule[rule]["fp"]
        fn = per_rule[rule]["fn"]
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        table.append({
            "rule": rule,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": None if precision is None else round(precision, 3),
            "recall": None if recall is None else round(recall, 3),
        })
    return {"per_rule": table}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Emit JSON output.")
    ap.add_argument(
        "--fixture", default=None,
        help="Run a single named fixture (directory name under corpus/).",
    )
    args = ap.parse_args()

    fixtures = sorted(
        d for d in CORPUS.iterdir() if d.is_dir() and (d / "page.html").is_file()
    )
    if args.fixture:
        fixtures = [d for d in fixtures if d.name == args.fixture]
        if not fixtures:
            print(f"fixture not found: {args.fixture}", file=sys.stderr)
            return 2

    per_fixture: dict[str, dict[str, Any]] = {}
    any_failed = False
    for fx in fixtures:
        url = (fx / "page.html").resolve().as_uri()
        truth = _load_yaml(fx / "ground_truth.yaml") if (fx / "ground_truth.yaml").is_file() else {}
        try:
            result = _audit(url)
        except Exception as exc:
            per_fixture[fx.name] = {
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            any_failed = True
            continue
        s = score_fixture(result, truth)
        per_fixture[fx.name] = s
        if not s["passed"]:
            any_failed = True

    summary = summarize(per_fixture)

    if args.json:
        print(json.dumps({"fixtures": per_fixture, "summary": summary}, indent=2))
        return 1 if any_failed else 0

    print("Benchmark results")
    print("=" * 70)
    for name, s in per_fixture.items():
        status = "PASS" if s.get("passed") else "FAIL"
        print(f"  [{status}] {name}")
        for m in s.get("missed_expected") or []:
            print(f"      missed: {m}")
        for f in s.get("false_positives") or []:
            print(f"      false positive: {f}")
        for o in s.get("over_fired") or []:
            print(f"      over-fired: {o}")
    print()
    print("Per-rule precision / recall")
    print("-" * 70)
    print(f"  {'rule':<40} {'tp':>4} {'fp':>4} {'fn':>4} {'P':>6} {'R':>6}")
    for row in summary["per_rule"]:
        p = "-" if row["precision"] is None else f"{row['precision']:.2f}"
        r = "-" if row["recall"] is None else f"{row['recall']:.2f}"
        print(
            f"  {row['rule']:<40} {row['tp']:>4} {row['fp']:>4} "
            f"{row['fn']:>4} {p:>6} {r:>6}"
        )
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
