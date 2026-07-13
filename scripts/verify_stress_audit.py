"""Run the full audit against the stress-test fixture and score
coverage per section of the fixture.

    python scripts/verify_stress_audit.py            # Path A only
    python scripts/verify_stress_audit.py --with-nvda  # + real NVDA (speaks)

The fixture has 20 deliberately-broken sections. For each we know
which rule (if any) should fire. Three outcomes are possible:
  - CAUGHT: at least one expected rule fired.
  - MISSED: an expected rule did not fire.
  - GAP   : there is no automated rule for this failure; documenting
           it as an honest limit of static analysis, not a tool bug.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Keep Path B opt-in even inside the orchestrator: we call it
# ourselves after the static pass so we can feed real tab stops.
os.environ.setdefault("SKIP_NVDA", "true")

from audit.orchestrator import AuditOrchestrator, run_nvda_follow_up  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "nvda_stress_page.html"

# (section, short label, expected rule(s), note). An empty tuple
# of rules = documented automated-analysis gap.
SECTIONS: list[tuple[int, str, tuple[str, ...], str]] = [
    (1,  "visible-text vs aria-label mismatch",
        ("sr-label-in-name", "sr-nvda-mismatch"),
        "Path A: WCAG 2.5.3 Label in Name. Path B: real NVDA override"),
    (2,  "visible label hidden with aria-hidden", ("sr-silent-interactive", "keyboard-no-accessible-name"),
        "aria-hidden on child strips the name from the tree"),
    (3,  "fake button (div role=button)", (),
        "a11y tree has a named button; no automated way to test that "
        "Enter/Space fires onclick"),
    (4,  "non-descriptive link text",
        ("cognitive-generic-link-text", "cognitive-duplicate-link-text"),
        "'Read more' / 'Click here' / duplicated"),
    (5,  "placeholder-as-label", ("forms-input-no-label",),
        "placeholder is not a label"),
    (6,  "aria-invalid without aria-describedby", ("forms-aria-invalid-no-description",),
        "error text exists but is not linked to the field"),
    (7,  "aria-hidden on focusable", ("aria-hidden-focusable",),
        "focus lands here, SR skips it"),
    (8,  "dangling aria-labelledby / describedby",
        ("aria-labelledby-missing", "aria-describedby-missing"),
        "references resolve to nothing"),
    (9,  "radio group without fieldset", ("forms-radio-group-no-fieldset",),
        "SR announces individual radios with no group name"),
    (10, "heading level skip", ("structure-heading-skip",),
        "h2 followed by h4"),
    (11, "data table without th", ("structure-table-no-th",),
        "first row uses td, not th"),
    (12, "image alt problems",
        ("media-img-no-alt", "media-img-placeholder-alt", "media-img-decorative-text"),
        "no alt / filename alt / 'image of ...' alt"),
    (13, "visual reading order reversed from DOM", (),
        "CSS flex-direction:row-reverse — not statically decidable"),
    (14, "required marker via colour only", (),
        "no reliable rule: any * or text could be required marker"),
    (15, "infinite animation, no reduced-motion guard",
        ("visual-infinite-animation", "preferences-no-reduced-motion-query",
         "preferences-reduced-motion-ignored"),
        "CSS animation + no @media query"),
    (16, "sub-minimum touch target", ("responsive-target-size",),
        "18x18px, under 24x24"),
    (17, "tiny text", ("visual-tiny-text",),
        "7px body text"),
    (18, "invalid ARIA role (typo)", ("aria-invalid-role",),
        "role=buton"),
    (19, "missing autocomplete on email/password", ("forms-missing-autocomplete",),
        "autocomplete is the bridge to password managers + speech input"),
    (20, "tooltip-only context (title=)", (),
        "button has visible name 'Danger' so it's not silent; NVDA may "
        "or may not speak the title depending on verbosity"),
    (21, "CSS ::before text skipped by SR", ("sr-browse-skipped-text",),
        "browse-mode (Path B): visible ghost-text not in a11y tree"),
    (22, "accordion doesn't update aria-expanded",
        ("dynamic-attribute-not-set",),
        "dynamic DSL: aria-expanded stays 'false' after click"),
    (23, "status change without live region",
        ("dynamic-live-region-silent",),
        "dynamic DSL: no aria-live on the status element"),
]

# Also always-on, page-scoped expectations (not tied to one section).
PAGE_LEVEL_EXPECTED: tuple[str, ...] = (
    "structure-no-main",            # no <main>
    "responsive-viewport-zoom-disabled",  # user-scalable=no in viewport meta
)


def run_path_a(url: str) -> dict:
    """Run the full Path A audit via AuditOrchestrator, including
    two dynamic-state probes configured via the interaction DSL."""
    options = {
        "skip_nvda": True,
        "headless": False,
        "interactions": [
            {
                "name": "accordion-aria-expanded-flip",
                "trigger_selector": "#broken-acc",
                "trigger_action": "click",
                "expect": {
                    "attribute_equals": {
                        "selector": "#broken-acc",
                        "name": "aria-expanded",
                        "value": "true",
                    },
                },
            },
            {
                "name": "status-live-region",
                "trigger_selector": "#save-btn",
                "trigger_action": "click",
                "expect": {"live_region_fires": "#save-status"},
            },
        ],
    }
    orch = AuditOrchestrator(url=url, options=options)
    return orch.run()


def run_path_b(url: str) -> dict:
    """Run the real-NVDA overlay with tab-stop alignment."""
    return run_nvda_follow_up(url, {"skip_nvda": False, "headless": False})


def summarize(result_a: dict, result_b: dict | None) -> int:
    all_issues = list(result_a.get("issues") or [])
    if result_b:
        all_issues.extend(result_b.get("issues") or [])

    # Group issues by rule for quick lookup.
    by_rule: dict[str, list[dict]] = defaultdict(list)
    for issue in all_issues:
        by_rule[issue["rule"]].append(issue)

    fired_rules = set(by_rule)

    # Summary score from the orchestrator (uses new level mapping).
    summary = result_a.get("summary") or {}
    print()
    print(f"Overall score        : {summary.get('score')}  ({summary.get('grade')})")
    by_level = summary.get("by_level") or {}
    if by_level:
        conf = summary.get("conformance") or {}
        print(
            f"  Level A   issues   : {by_level.get('A', {}).get('issues', 0)}"
            f"   {'(A-conformant)' if conf.get('A_conformant') else '(A fails)'}"
        )
        print(
            f"  Level AA  issues   : {by_level.get('AA', {}).get('issues', 0)}"
            f"   {'(AA-conformant)' if conf.get('AA_conformant') else '(AA fails)'}"
        )
        print(
            f"  Level AAA issues   : {by_level.get('AAA', {}).get('issues', 0)}"
            f"   {'(AAA-conformant)' if conf.get('AAA_conformant') else '(AAA fails)'}"
        )
        print(f"  Unmapped           : {by_level.get('unmapped', {}).get('issues', 0)}")
    print()
    print(f"Total issues emitted : {len(all_issues)}")
    print(f"Distinct rules fired : {len(fired_rules)}")
    modules_ran = {m: info for m, info in (result_a.get("modules") or {}).items()}
    bad_modules = [
        f"{m} (err={info.get('error')[:60]!r})" for m, info in modules_ran.items()
        if info.get("error")
    ]
    if bad_modules:
        print("Module errors        :", ", ".join(bad_modules))
    print()

    # ------------- by module -----------------
    print("Issues by module")
    print("-" * 60)
    module_counts: dict[str, int] = defaultdict(int)
    for issue in all_issues:
        module_counts[issue.get("module", "?")] += 1
    for m in sorted(module_counts):
        print(f"  {m:<16} {module_counts[m]}")
    print()

    # ------------- per-section coverage ------
    print("Per-section coverage")
    print("=" * 78)
    print(f"  {'#':<3}{'section':<44}{'status':<8} note")
    print("  " + "-" * 74)

    caught = missed = gap = 0
    for num, label, rules, note in SECTIONS:
        if not rules:
            status = "GAP"
            gap += 1
        elif any(r in fired_rules for r in rules):
            status = "CAUGHT"
            caught += 1
        else:
            status = "MISSED"
            missed += 1
        # Truncate label / note for readable columns.
        trim_label = label if len(label) <= 42 else label[:41] + "…"
        trim_note = note if len(note) <= 50 else note[:49] + "…"
        print(f"  {num:<3}{trim_label:<44}{status:<8} {trim_note}")

    print("  " + "-" * 74)
    total_testable = caught + missed
    print(f"  score: {caught}/{total_testable} testable sections caught "
          f"(+ {gap} documented automation gaps)")
    print()

    # ------------- page-scoped expectations --
    print("Page-level expectations")
    print("-" * 60)
    for rule in PAGE_LEVEL_EXPECTED:
        status = "CAUGHT" if rule in fired_rules else "missed"
        print(f"  {status:<8} {rule}")
    print()

    # ------------- Path B detail -------------
    if result_b:
        print("Path B (real NVDA) detail")
        print("-" * 60)
        nvda = result_b.get("nvda") or {}
        print(f"  status               : {result_b.get('nvda_status')}")
        print(f"  utterances captured  : {nvda.get('utterances_captured')}")
        print(f"  tab stops            : {nvda.get('tab_stops')}")
        print(f"  log bytes            : {nvda.get('log_bytes')}")
        mismatches = [i for i in (result_b.get('issues') or []) if i['rule'] == 'sr-nvda-mismatch']
        if mismatches:
            print("  sr-nvda-mismatch samples:")
            for i in mismatches[:5]:
                details = i.get('details') or {}
                dom = (details.get('dom_name') or '')[:40]
                spoken = (details.get('nvda_spoken') or '')[:40]
                print(f"    - dom={dom!r:42s} nvda={spoken!r}")
        print()

    # ------------- any unexpected rule fires -
    expected = {r for _, _, rs, _ in SECTIONS for r in rs} | set(PAGE_LEVEL_EXPECTED)
    # axe-core rules are prefixed with "wcag-" or similar by wcag_engine;
    # we treat them as informational "extras" since we didn't pre-list them.
    unexpected = fired_rules - expected
    if unexpected:
        print(f"Unexpected rule fires (informational; {len(unexpected)} rules):")
        print("-" * 60)
        for rule in sorted(unexpected):
            n = len(by_rule[rule])
            module = by_rule[rule][0].get("module", "?")
            print(f"  [{module:<14}] {rule} ({n}x)")

    # Exit non-zero only if a *testable* section was missed.
    return 1 if missed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-nvda", action="store_true",
                    help="also run Path B (launches NVDA; machine speaks)")
    args = ap.parse_args()

    url = FIXTURE.as_uri()
    print(f"fixture: {url}")
    print("running Path A (full orchestrator)...")
    t0 = time.time()
    result_a = run_path_a(url)
    print(f"  done in {time.time()-t0:.1f}s\n")

    result_b = None
    if args.with_nvda:
        print("running Path B (real NVDA)...")
        t1 = time.time()
        result_b = run_path_b(url)
        print(f"  done in {time.time()-t1:.1f}s\n")

    return summarize(result_a, result_b)


if __name__ == "__main__":
    raise SystemExit(main())
