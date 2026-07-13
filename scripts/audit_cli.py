"""Command-line audit runner with CI-friendly exit codes.

Usage:
    python scripts/audit_cli.py <url> [options]

Exit codes (so CI pipelines can gate on accessibility):
    0  success (no issues at or above --fail-level)
    1  issues at/above --fail-level present
    2  audit itself failed (browser launch, unreachable target, etc.)
    3  invalid arguments / config

Examples:
    # Fail the build only on Level A violations
    python scripts/audit_cli.py https://example.com --fail-level A

    # Fail on any issue at or above AA (default).
    python scripts/audit_cli.py https://example.com --fail-level AA

    # Fail only if specific severities are present
    python scripts/audit_cli.py https://example.com --fail-on critical,serious
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.orchestrator import AuditOrchestrator  # noqa: E402

_LEVEL_RANK = {"A": 0, "AA": 1, "AAA": 2}


def _level_fails(issue_level: str | None, threshold: str) -> bool:
    """True when `issue_level` is as strict or stricter than `threshold`.

    We mark the issue as failing when its level is <= threshold in the
    A < AA < AAA ordering (lower numerical rank = stricter). Issues
    without a mapped level are ignored for level gating — they still
    appear in the report but don't gate the build, because we don't
    know their WCAG severity.
    """
    if not issue_level:
        return False
    return _LEVEL_RANK.get(issue_level, 99) <= _LEVEL_RANK[threshold]


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an accessibility audit and exit with a CI-friendly status code.")
    ap.add_argument("url", help="URL to audit (http/https or file://).")
    ap.add_argument(
        "--fail-level",
        choices=("A", "AA", "AAA", "off"),
        default="AA",
        help="Exit non-zero when any issue at this level or stricter is found "
             "(default: AA). Use 'off' to never fail on level.",
    )
    ap.add_argument(
        "--fail-on",
        default="",
        help="Comma-separated severities that should fail (e.g. 'critical,serious'). "
             "Combined with --fail-level with OR semantics.",
    )
    ap.add_argument(
        "--headless", action="store_true", default=True,
        help="Run the browser headless (default). Use --no-headless for a visible browser.",
    )
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.add_argument(
        "--skip-nvda", action="store_true", default=True,
        help="Skip Path B (real NVDA). Default on non-Windows or when no Windows worker.",
    )
    ap.add_argument("--with-nvda", dest="skip_nvda", action="store_false")
    ap.add_argument(
        "--output", "-o", default="-",
        help="Path to write the JSON audit result (default: stdout). Use '-' for stdout.",
    )
    ap.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress the human summary on stderr; useful when piping JSON.",
    )
    args = ap.parse_args()

    fail_severities = {s.strip() for s in args.fail_on.split(",") if s.strip()}

    options = {"skip_nvda": args.skip_nvda, "headless": args.headless}
    try:
        result = AuditOrchestrator(url=args.url, options=options).run()
    except Exception as exc:
        print(f"audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # Emit the raw JSON to the requested sink.
    payload = json.dumps(result, indent=2, default=str)
    if args.output == "-":
        print(payload)
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")

    # Human summary on stderr (doesn't pollute stdout when piping).
    if not args.quiet:
        summary = result.get("summary") or {}
        by_level = summary.get("by_level") or {}
        by_sev = summary.get("by_severity") or {}
        conformance = summary.get("conformance") or {}
        print(file=sys.stderr)
        print(f"audit: {args.url}", file=sys.stderr)
        print(f"  score     : {summary.get('score')} ({summary.get('grade')})", file=sys.stderr)
        print(f"  total     : {summary.get('total_issues')} issues", file=sys.stderr)
        print(
            f"  severity  : "
            f"{by_sev.get('critical', 0)} critical / "
            f"{by_sev.get('serious', 0)} serious / "
            f"{by_sev.get('moderate', 0)} moderate / "
            f"{by_sev.get('minor', 0)} minor",
            file=sys.stderr,
        )
        print(
            f"  WCAG      : A={by_level.get('A', {}).get('issues', 0)} "
            f"AA={by_level.get('AA', {}).get('issues', 0)} "
            f"AAA={by_level.get('AAA', {}).get('issues', 0)} "
            f"(A={'PASS' if conformance.get('A_conformant') else 'FAIL'}, "
            f"AA={'PASS' if conformance.get('AA_conformant') else 'FAIL'})",
            file=sys.stderr,
        )

    # Exit decision.
    issues = result.get("issues") or []
    should_fail = False
    if args.fail_level != "off":
        if any(_level_fails(i.get("level"), args.fail_level) for i in issues):
            should_fail = True
    if fail_severities and any(i.get("severity") in fail_severities for i in issues):
        should_fail = True

    if should_fail:
        print(f"\naudit: FAIL (threshold --fail-level={args.fail_level}"
              f"{' --fail-on=' + args.fail_on if fail_severities else ''})", file=sys.stderr)
        return 1
    print("\naudit: PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
