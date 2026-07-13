"""Run the accessibility analyzers against scripts/nvda_test_page.html
and report which deliberate issues were caught.

Usage:
    python scripts/verify_nvda_rules.py            # Path A only (safe, no NVDA)
    python scripts/verify_nvda_rules.py --with-nvda  # Path A + Path B (launches NVDA)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import keyboard as keyboard_module  # noqa: E402
from audit import screen_reader  # noqa: E402
from audit.browser import _CHANNEL_FALLBACKS, LAUNCH_ARGS  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "nvda_test_page.html"

# Rules the fixture page is designed to trigger. Used to measure
# coverage — every key should fire at least once in a healthy run.
EXPECTED_RULES_PATH_A = {
    "sr-silent-interactive": "silent button with no label",
    "sr-empty-heading": "empty h3",
    "sr-duplicate-landmark": "two <nav> without aria-label",
    "sr-dialog-no-name": "role=dialog with no accessible name",
    "keyboard-generic-focusable": "<div tabindex=0> with no role",
    "keyboard-positive-tabindex": "<a tabindex=3>",
    "keyboard-no-focus-indicator": "button with outline:none",
    "keyboard-no-accessible-name": "silent button (keyboard path)",
}
EXPECTED_RULES_PATH_B = {
    "sr-nvda-mismatch": "icon button with aria-label != visible text",
}


def launch_chromium(pw, headless: bool):
    last_exc = None
    for channel in _CHANNEL_FALLBACKS:
        kwargs = {"headless": headless, "args": LAUNCH_ARGS}
        if channel:
            kwargs["channel"] = channel
        try:
            b = pw.chromium.launch(**kwargs)
            print(f"  launched Chromium (headless={headless}, channel={channel or 'bundled'})")
            return b
        except Exception as exc:
            msg = str(exc)
            if "spawn UNKNOWN" not in msg and "side-by-side" not in msg:
                raise
            last_exc = exc
    raise RuntimeError("no Chromium channel usable") from last_exc


def run_path_a(page):
    """Run the static analyzers (a11y-tree + keyboard tab-walk) and
    collect the issues they emit."""
    print("\n--- Path A: Chromium a11y-tree + keyboard walk ---")
    issues: list[dict] = []

    sr_result = screen_reader.run(page, {})
    issues.extend(sr_result.get("issues") or [])
    print(
        f"  screen_reader.run   ran={sr_result.get('ran')} "
        f"issues={len(sr_result.get('issues') or [])} "
        f"tree_nodes={sr_result.get('tree_nodes')}"
    )

    stops, cycled = keyboard_module._walk(page, {"max_tabs": 20, "wait_ms": 20})
    kb_issues = keyboard_module.analyze(stops, cycled, 20)
    issues.extend(kb_issues)
    print(f"  keyboard walk       stops={len(stops)} issues={len(kb_issues)}")

    return issues, stops


def run_path_b(page, stops):
    """Run the real-NVDA overlay. Returns the raw analyze_results dict
    or None if NVDA couldn't be started."""
    print("\n--- Path B: live NVDA capture ---")
    nvda = screen_reader.NVDAController()
    try:
        nvda.ensure_running()
    except screen_reader.NVDAUnavailableError as exc:
        print(f"  SKIPPED: {exc}")
        return None

    print(f"  NVDA up, log={nvda._log_path}, via_task={nvda._launched_via_task}")

    try:
        page.bring_to_front()
        try:
            page.mouse.click(10, 10)
        except Exception:
            pass
        # Let NVDA finish announcing the page load before we start
        # capturing — same trick as in the orchestrator.
        time.sleep(2.0)
        nvda.start_capture()
        # Re-walk with the NVDA capture window open.
        _stops, _cycled = keyboard_module._walk(page, {"max_tabs": 20, "wait_ms": 350})
        time.sleep(nvda.PER_STOP_SPEECH_WAIT * 2)
        nvda.stop_capture()
        return nvda.analyze_results(stops)
    finally:
        nvda.shutdown()


def summarize(path_a_issues, path_b_result, expect_b: bool):
    print("\n" + "=" * 62)
    print("COVERAGE REPORT")
    print("=" * 62)

    fired = {i["rule"] for i in path_a_issues}
    if path_b_result:
        fired.update(i["rule"] for i in path_b_result.get("issues") or [])

    rows = []
    for rule, desc in EXPECTED_RULES_PATH_A.items():
        rows.append(("A", rule, desc, rule in fired))
    if expect_b:
        for rule, desc in EXPECTED_RULES_PATH_B.items():
            rows.append(("B", rule, desc, rule in fired))

    caught = sum(1 for _, _, _, ok in rows if ok)
    total = len(rows)
    print(f"\n{caught}/{total} expected rules fired\n")
    print(f"  {'path':<6}{'rule':<32}{'status':<9} fixture")
    print("  " + "-" * 58)
    for path, rule, desc, ok in rows:
        status = "CAUGHT" if ok else "missed"
        print(f"  {path:<6}{rule:<32}{status:<9} {desc}")

    # Surface any unexpected extras (not in our expected map) so we
    # know if the analyzer is firing something we didn't plan for.
    extras = fired - set(EXPECTED_RULES_PATH_A) - set(EXPECTED_RULES_PATH_B)
    if extras:
        print("\n  unexpected rule fires (informational):")
        for rule in sorted(extras):
            n = sum(1 for i in path_a_issues if i["rule"] == rule) + (
                sum(1 for i in (path_b_result or {}).get("issues", []) if i["rule"] == rule)
                if path_b_result
                else 0
            )
            print(f"    - {rule} ({n}x)")

    if path_b_result and path_b_result.get("ran"):
        print(
            f"\n  Path B transcript: {path_b_result.get('utterances_captured')} "
            f"utterances across {path_b_result.get('tab_stops')} stops"
        )

    return caught == total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--with-nvda",
        action="store_true",
        help="Also run Path B (launches NVDA; your machine will speak).",
    )
    args = ap.parse_args()

    url = FIXTURE.as_uri()
    print(f"fixture: {url}")

    # Path A can run headless (doesn't need NVDA); Path B needs a
    # visible window so the OS has something to speak about.
    headless = not args.with_nvda

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = launch_chromium(pw, headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            a_issues, stops = run_path_a(page)

            print("\nissues emitted (Path A):")
            for i in a_issues:
                print(f"  [{i['severity']:<8}] {i['rule']:<32} {i['title'][:60]}")

            b_result = None
            if args.with_nvda:
                b_result = run_path_b(page, stops)
                if b_result:
                    print("\nissues emitted (Path B):")
                    for i in b_result.get("issues") or []:
                        print(f"  [{i['severity']:<8}] {i['rule']:<32} {i['title'][:60]}")
        finally:
            browser.close()

    ok = summarize(a_issues, b_result, expect_b=args.with_nvda)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
