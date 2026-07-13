"""End-to-end accessibility report generator.

Pipeline:
    1. Run the full orchestrator against `url`.
    2. (optional) Enrich issues via OpenRouter AI.
    3. Write JSON + XLSX outputs to disk, and optionally print a
       compact human summary to stdout.

Usage:
    # Basic: run audit, write JSON + XLSX next to each other.
    python scripts/audit_report.py https://example.com -o out/example

    # With AI enrichment (needs OPENROUTER_API_KEY in env).
    python scripts/audit_report.py https://example.com -o out/example --enrich

    # Different WCAG target + enrichment model.
    python scripts/audit_report.py https://example.com -o out/example \\
        --enrich --target-level AAA \\
        --model anthropic/claude-3.5-sonnet
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description="Full audit + enrichment + XLSX export.")
    ap.add_argument("url", help="URL to audit (http/https/file).")
    ap.add_argument(
        "-o", "--output", default="out/audit",
        help="Output path prefix. Produces <prefix>.json and <prefix>.xlsx.",
    )
    ap.add_argument(
        "--enrich", action="store_true",
        help="Call OpenRouter to add location_guide / reproduction / "
             "recommendation / user_impact to each issue.",
    )
    ap.add_argument("--model", default=None, help="OpenRouter model id.")
    ap.add_argument("--batch-size", type=int, default=None, help="Issues per AI request.")
    ap.add_argument(
        "--target-level", choices=("A", "AA", "AAA"), default="AA",
        help="WCAG conformance level used by the VPAT scorecard sheet.",
    )
    ap.add_argument("--with-nvda", dest="skip_nvda", action="store_false", default=True)
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.add_argument("--quiet", "-q", action="store_true")
    args = ap.parse_args()

    from audit.orchestrator import AuditOrchestrator
    from audit.export_xlsx import save_xlsx
    from audit.ai_enrich import enrich_issues

    t0 = time.time()
    options = {"skip_nvda": args.skip_nvda, "headless": args.headless}

    if not args.quiet:
        print(f"auditing {args.url}...", file=sys.stderr)
    try:
        result = AuditOrchestrator(url=args.url, options=options).run()
    except Exception as exc:
        print(f"audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.enrich:
        if not args.quiet:
            print(f"enriching {len(result.get('issues') or [])} issues via OpenRouter...", file=sys.stderr)
        enriched = enrich_issues(
            result.get("issues") or [],
            model=args.model,
            batch_size=args.batch_size,
        )
        result["issues"] = enriched
        # Record enrichment metadata so downstream consumers know what
        # was done without re-reading each issue's ai_enriched flag.
        enriched_count = sum(1 for i in enriched if i.get("ai_enriched"))
        result.setdefault("meta", {})["ai_enrichment"] = {
            "requested": True,
            "issues_enriched": enriched_count,
            "model": os.environ.get("OPENROUTER_MODEL") or args.model or "openai/gpt-4o-mini",
        }

    # Ensure output directory exists and write both formats.
    out_prefix = Path(args.output)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    xlsx_path = out_prefix.with_suffix(".xlsx")
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    save_xlsx(result, str(xlsx_path), target_level=args.target_level)

    elapsed = time.time() - t0
    if not args.quiet:
        summary = result.get("summary") or {}
        by_level = summary.get("by_level") or {}
        print(file=sys.stderr)
        print(f"done in {elapsed:.1f}s", file=sys.stderr)
        print(f"  score      : {summary.get('score')} ({summary.get('grade')})", file=sys.stderr)
        print(f"  issues     : {summary.get('total_issues')}", file=sys.stderr)
        print(
            f"  WCAG       : A={by_level.get('A', {}).get('issues', 0)} "
            f"AA={by_level.get('AA', {}).get('issues', 0)} "
            f"AAA={by_level.get('AAA', {}).get('issues', 0)}",
            file=sys.stderr,
        )
        print(f"  JSON       : {json_path}", file=sys.stderr)
        print(f"  XLSX       : {xlsx_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
