"""Live smoke test: launch NVDA, open a page with Playwright, tab-walk,
capture speech, parse it. Run from the repo root.

    python scripts/smoke_test_nvda.py

Your machine will speak out loud for ~5 seconds during the run.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.browser import _CHANNEL_FALLBACKS, LAUNCH_ARGS  # noqa: E402
from audit import keyboard as keyboard_module  # noqa: E402
from audit.screen_reader import NVDAController, _find_nvda_executable  # noqa: E402


def launch_headful(pw):
    """Launch a visible chromium by walking the same channel fallback list
    that audit/browser.py uses, so the test matches production behaviour."""
    last_exc = None
    for channel in _CHANNEL_FALLBACKS:
        try:
            kwargs = {"headless": False, "args": LAUNCH_ARGS}
            if channel:
                kwargs["channel"] = channel
            browser = pw.chromium.launch(**kwargs)
            print(f"  launched Chromium via channel={channel or 'bundled'}")
            return browser
        except Exception as exc:
            msg = str(exc)
            if "spawn UNKNOWN" not in msg and "side-by-side" not in msg:
                raise
            last_exc = exc
    raise RuntimeError("no Chromium channel usable") from last_exc


def main() -> int:
    exe = _find_nvda_executable()
    print(f"NVDA exe : {exe}")
    if not exe:
        print("ERROR: no nvda.exe found")
        return 1

    print("\n--- ensure_running ---")
    nvda = NVDAController()
    t0 = time.time()
    nvda.ensure_running()
    print(
        f"  up in {time.time() - t0:.1f}s  via_task={nvda._launched_via_task}  "
        f"log={nvda._log_path}"
    )

    print("\n--- opening a test page + tab-walking ---")
    html = """
<!doctype html>
<html lang=en><head><title>NVDA smoke test</title></head>
<body>
  <h1>Smoke test</h1>
  <a href="#one">First link labelled one</a>
  <button>Second button labelled two</button>
  <input type="text" aria-label="Third email field" />
  <a href="#last">Fourth link labelled four</a>
</body></html>
"""
    from playwright.sync_api import sync_playwright

    stops: list = []
    with sync_playwright() as pw:
        browser = launch_headful(pw)
        try:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_content(html)
            page.bring_to_front()
            # Click inside the page to force the browser window to own
            # focus. bring_to_front() alone is not reliable on Windows
            # when another window (Settings, Search, Terminal) has
            # keyboard focus — NVDA would announce that other app
            # instead of our browser-driven tab walk.
            try:
                page.mouse.click(10, 10)
            except Exception:
                pass
            # Let NVDA finish its page-load preamble ("window", "region",
            # "document") BEFORE we start capturing — otherwise those
            # utterances misalign with tab stops downstream.
            time.sleep(2.0)
            nvda.start_capture()
            stops, _cycled = keyboard_module._walk(
                page,
                {"max_tabs": 6, "wait_ms": int(nvda.PER_STOP_SPEECH_WAIT * 1000)},
            )
            time.sleep(nvda.PER_STOP_SPEECH_WAIT * 2)  # flush tail speech
            nvda.stop_capture()
        finally:
            browser.close()

    print(f"  tab stops collected: {len(stops)}")
    for i, s in enumerate(stops):
        print(f"   [{i}] {s.get('selector')}  accName={s.get('accessible_name')!r}")

    print("\n--- analyze_results ---")
    result = nvda.analyze_results(stops)
    print(f"  log bytes          : {result['log_bytes']}")
    print(f"  utterances         : {result['utterances_captured']}")
    print(f"  issues             : {len(result['issues'])}")

    for item in result["nvda_transcript"][:10]:
        print(
            f"   [{item['index']}]  dom={item['dom_name']!r:40s}  "
            f"nvda={item['nvda_spoken']!r}"
        )
    for issue in result["issues"]:
        print(f"  ISSUE: {issue['rule']} @ {issue.get('selector')}: {issue['title']}")

    raw = nvda._read_captured_log()
    speaking = [ln for ln in raw.splitlines() if "Speaking" in ln]
    print(f"\n--- raw log: {len(speaking)} Speaking[] lines ---")
    for ln in speaking[:10]:
        print(" >", ln.strip()[:240])

    print("\n--- shutdown ---")
    nvda.shutdown()
    print(f"total elapsed: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
