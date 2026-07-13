"""Diff two audit JSON outputs to surface what changed.

Usage:
    python scripts/audit_diff.py old.json new.json
    python scripts/audit_diff.py old.json new.json --exit-on-regression

An issue is matched across audits by (rule, selector). New issues
that appear in the newer run are regressions; issues only in the older
run are fixes. Changes to existing issues (different severity,
different WCAG criteria after dedup) are flagged as "modified".

Exit codes:
    0  no regressions
    1  at least one new issue (regression)
    2  bad input
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _issue_key(issue: dict[str, Any]) -> tuple[str, str]:
    """Stable identity key for matching issues across runs.

    Prefers the issue's `fingerprint` (rule+selector+wcag-criteria
    hash) when present — it survives rewording of titles/descriptions
    that (rule, selector) tuples don't. Falls back to (rule, selector)
    for pre-fingerprint reports.
    """
    fp = issue.get("fingerprint")
    if fp:
        return ("fp", fp)
    selector = (issue.get("element") or {}).get("selector", "")
    return (issue.get("rule", ""), selector)


# Severity-weighted regression score. The weights mirror
# audit/scorer.py's penalties so that a "regression score" lines up
# with the audit's headline score — a `critical` regression is felt
# 10× more than a `minor` one. Adjust here in lockstep with scorer.py
# whenever the score weights change.
_SEV_WEIGHT = {
    "critical": 10,
    "serious": 4,
    "moderate": 2,
    "minor": 1,
}


def _weight(issue: dict[str, Any]) -> int:
    return _SEV_WEIGHT.get((issue.get("severity") or "").lower(), 1)


def _confidence_factor(issue: dict[str, Any]) -> float:
    """Down-weight low-confidence regressions in the score.

    Mirrors `audit/scorer.py`: low-confidence findings count at 0.5×,
    medium at 0.8×, high at 1×. A heuristic regression is real signal
    but shouldn't fail CI as hard as a deterministic one.
    """
    return {"low": 0.5, "medium": 0.8}.get(
        (issue.get("confidence") or "high").lower(), 1.0
    )


def _load(path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"failed to read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_issues = {_issue_key(i): i for i in (old.get("issues") or [])}
    new_issues = {_issue_key(i): i for i in (new.get("issues") or [])}

    regressions = [new_issues[k] for k in new_issues if k not in old_issues]
    fixes = [old_issues[k] for k in old_issues if k not in new_issues]

    # Same (rule, selector) in both — flag as "modified" only when
    # severity OR level changed. Other field drift (description, fix
    # text) is usually rule-authoring churn, not a semantic diff.
    modified: list[dict[str, Any]] = []
    for k in set(old_issues) & set(new_issues):
        o, n = old_issues[k], new_issues[k]
        if o.get("severity") != n.get("severity") or o.get("level") != n.get("level"):
            modified.append({
                "rule": n.get("rule"),
                "selector": k[1],
                "before": {"severity": o.get("severity"), "level": o.get("level")},
                "after": {"severity": n.get("severity"), "level": n.get("level")},
            })

    old_sum = old.get("summary") or {}
    new_sum = new.get("summary") or {}

    regression_score = sum(
        _weight(r) * _confidence_factor(r) for r in regressions
    )
    fix_score = sum(_weight(r) * _confidence_factor(r) for r in fixes)
    net_score = regression_score - fix_score

    by_severity_delta: dict[str, dict[str, int]] = {}
    for sev in ("critical", "serious", "moderate", "minor"):
        by_severity_delta[sev] = {
            "regressions": sum(1 for r in regressions if (r.get("severity") or "").lower() == sev),
            "fixes":       sum(1 for r in fixes       if (r.get("severity") or "").lower() == sev),
        }

    # Rule-set drift: did the rule logic itself change between runs?
    # If so, "regressions" may be artefacts of a tightened rule
    # rather than real new defects. Surface this so reviewers can
    # contextualise.
    rule_set_changed = bool(
        old.get("rule_set_hash") and new.get("rule_set_hash")
        and old.get("rule_set_hash") != new.get("rule_set_hash")
    )

    return {
        "regressions": regressions,
        "fixes": fixes,
        "modified": modified,
        "score_delta": {
            "before": old_sum.get("score"),
            "after": new_sum.get("score"),
            "change": (new_sum.get("score") or 0) - (old_sum.get("score") or 0),
        },
        "by_level_delta": _level_delta(old_sum, new_sum),
        "by_severity_delta": by_severity_delta,
        # Severity-weighted regression score. Positive numbers are
        # bad; CI gates can fail when this exceeds a configurable
        # threshold instead of failing on any single new issue.
        "regression_score": round(regression_score, 1),
        "fix_score": round(fix_score, 1),
        "net_regression_score": round(net_score, 1),
        "rule_set_changed": rule_set_changed,
        "rule_set_hash": {
            "before": old.get("rule_set_hash"),
            "after": new.get("rule_set_hash"),
        },
    }


def _level_delta(old_sum: dict[str, Any], new_sum: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lvl in ("A", "AA", "AAA"):
        b = (old_sum.get("by_level") or {}).get(lvl, {}).get("issues", 0)
        a = (new_sum.get("by_level") or {}).get(lvl, {}).get("issues", 0)
        out[lvl] = {"before": b, "after": a, "change": a - b}
    return out


def _print_human(d: dict[str, Any]) -> None:
    print(f"Regressions : {len(d['regressions'])}  (weighted: {d['regression_score']})")
    print(f"Fixes       : {len(d['fixes'])}  (weighted: {d['fix_score']})")
    print(f"Net score   : {d['net_regression_score']:+}  (positive = worse)")
    print(f"Modified    : {len(d['modified'])}")
    if d.get("rule_set_changed"):
        print(
            "WARNING: rule set changed between runs — some "
            "'regressions' may be the rule itself getting tighter."
        )
    sd = d["score_delta"]
    # Score might be None if summary is missing — guard the delta
    # formatter to avoid crashing when diffing a partial audit.
    try:
        change_str = f"{int(sd['change']):+d}"
    except (TypeError, ValueError):
        change_str = str(sd.get("change"))
    print(f"Score       : {sd['before']} -> {sd['after']}  (delta {change_str})")
    for lvl, info in d["by_level_delta"].items():
        print(
            f"  Level {lvl:<3}: {info['before']} -> {info['after']}  "
            f"(delta {info['change']:+d})"
        )
    if d["regressions"]:
        print("\nNew issues (regressions):")
        for r in d["regressions"][:20]:
            sel = (r.get("element") or {}).get("selector", "")
            print(f"  [{r.get('severity'):<8}] {r.get('rule'):<32} {sel}")
        if len(d["regressions"]) > 20:
            print(f"  ... and {len(d['regressions']) - 20} more")
    if d["fixes"]:
        print("\nResolved issues (fixes):")
        for r in d["fixes"][:10]:
            sel = (r.get("element") or {}).get("selector", "")
            print(f"  [{r.get('severity'):<8}] {r.get('rule'):<32} {sel}")
        if len(d["fixes"]) > 10:
            print(f"  ... and {len(d['fixes']) - 10} more")


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff two audit JSON outputs.")
    ap.add_argument("old", help="Older audit JSON file.")
    ap.add_argument("new", help="Newer audit JSON file.")
    ap.add_argument("--json", action="store_true", help="Emit the full diff as JSON.")
    ap.add_argument(
        "--exit-on-regression", action="store_true",
        help="Exit 1 when any new issue appears in the newer run.",
    )
    ap.add_argument(
        "--max-regression-score", type=float, default=None,
        help=(
            "Exit 1 when the severity-weighted net regression score "
            "(regressions - fixes) exceeds this value. Useful for CI "
            "gates that tolerate small regressions while fixing other "
            "issues. Default: disabled."
        ),
    )
    ap.add_argument(
        "--severity-floor",
        choices=["critical", "serious", "moderate", "minor"],
        default=None,
        help=(
            "Only count regressions at or above this severity for the "
            "exit code. E.g. `--severity-floor serious` lets minor "
            "regressions land while gating on serious ones."
        ),
    )
    args = ap.parse_args()

    d = diff(_load(args.old), _load(args.new))

    if args.json:
        print(json.dumps(d, indent=2, default=str))
    else:
        _print_human(d)

    sev_rank = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
    floor = sev_rank.get(args.severity_floor, 99)
    gating_regressions = [
        r for r in d["regressions"]
        if sev_rank.get((r.get("severity") or "").lower(), 99) <= floor
    ]
    if args.max_regression_score is not None:
        if d["net_regression_score"] > args.max_regression_score:
            return 1
    if args.exit_on_regression and gating_regressions:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
