"""End-to-end integration test.

Serves tests/fixtures/issues_sample.html over a local HTTP server, runs
both the quick (axe-core only) and full-orchestrator audits against it,
and asserts that representative rules from each module fire.

Skipped automatically when:
- Playwright isn't importable, or
- The Chromium binary isn't installed, or
- axe-core hasn't been vendored locally (no network fallback in tests).

Marked `slow` — opt out with `pytest -m "not slow"`.
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent.parent
VENDOR_AXE = PROJECT_ROOT / "vendor" / "axe.min.js"

pytestmark = pytest.mark.slow

# Skip the whole module if Playwright or its browsers aren't available.
playwright = pytest.importorskip("playwright.sync_api")

if not VENDOR_AXE.exists():
    pytest.skip(
        "vendor/axe.min.js not found — run `python scripts/fetch_axe.py` first",
        allow_module_level=True,
    )


def _chromium_available() -> bool:
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
            return True
    except Exception:
        return False


if not _chromium_available():
    pytest.skip(
        "Chromium not available — run `playwright install chromium`",
        allow_module_level=True,
    )


# --- Local HTTP server fixture ---------------------------------------------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # noqa: ARG002
        pass


@pytest.fixture(scope="module")
def fixture_server():
    """Serve tests/fixtures/ on an ephemeral port for the duration of the module.

    We bind the directory explicitly rather than leaning on os.chdir —
    SimpleHTTPRequestHandler reads CWD at request time, and other parts of
    the test stack (database init, logging) may change CWD concurrently.
    """
    handler = functools.partial(_QuietHandler, directory=str(FIXTURE_DIR))

    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


@pytest.fixture(scope="module")
def sample_url(fixture_server):
    return f"{fixture_server}/issues_sample.html"


# --- Tests -----------------------------------------------------------------


def _rules_in(issues):
    return {i["rule"] for i in issues}


def test_quick_audit_reports_axe_violations(sample_url):
    """/audit/quick runs axe-core only and should find multiple issues."""
    from audit.orchestrator import run_quick_audit

    result = run_quick_audit(sample_url, options={"level": "aa", "timeout_seconds": 30})

    assert result["mode"] == "quick"
    wcag = result["modules"]["wcag_engine"]
    assert wcag["ran"] is True, wcag
    # The fixture plants enough issues that axe should flag several.
    assert wcag["issues_found"] >= 3, f"expected axe to catch >=3 issues, got {wcag}"
    # Score shouldn't be perfect given the planted issues.
    assert result["summary"]["score"] < 100
    assert result["summary"]["grade"] != "A"


def test_full_orchestrator_runs_every_module(sample_url):
    """Full AuditOrchestrator runs all 9 modules and returns issues per module."""
    from audit.orchestrator import AuditOrchestrator

    result = AuditOrchestrator(
        url=sample_url,
        options={"level": "aa", "timeout_seconds": 30, "max_tabs": 30, "wait_ms": 10},
    ).run()

    # Every module we ship should have executed.
    modules = result["modules"]
    expected = {
        "wcag_engine",
        "structure",
        "aria",
        "media",
        "cognitive",
        "keyboard",
        "forms",
        "responsive",
        "visual",
        "screen_reader",
    }
    assert set(modules.keys()) == expected, modules.keys()

    # Every module should have ran=True (no exceptions).
    for name, m in modules.items():
        assert m["ran"] is True, f"module {name} did not run: {m}"


def test_full_orchestrator_catches_planted_rules(sample_url):
    """Assert representative rules from each module are present in the output.

    We assert rule *names* rather than exact counts — axe versions and
    deduplication shift individual counts but the rule identity is stable.
    """
    from audit.orchestrator import AuditOrchestrator

    result = AuditOrchestrator(
        url=sample_url,
        options={"level": "aa", "timeout_seconds": 30, "max_tabs": 30, "wait_ms": 10},
    ).run()

    rules = _rules_in(result["issues"])

    # structure
    assert "structure-html-lang" in rules
    assert "structure-title-missing" in rules
    assert "structure-no-h1" in rules
    assert "structure-heading-skip" in rules
    assert "structure-no-main" in rules
    assert "structure-table-no-th" in rules

    # aria
    assert "aria-invalid-role" in rules
    assert "aria-labelledby-missing" in rules
    assert "aria-hidden-focusable" in rules

    # media
    assert "media-img-no-alt" in rules
    assert "media-img-placeholder-alt" in rules
    assert "media-img-decorative-text" in rules
    assert "media-video-no-track" in rules

    # cognitive
    assert "cognitive-generic-link-text" in rules
    assert "cognitive-empty-link" in rules
    assert "cognitive-duplicate-link-text" in rules

    # keyboard
    assert "keyboard-positive-tabindex" in rules
    assert "keyboard-generic-focusable" in rules
    # We don't assert every keyboard rule — focus-indicator detection is
    # browser-timing-sensitive.

    # forms
    assert "forms-input-no-label" in rules
    assert "forms-radio-group-no-fieldset" in rules
    assert "forms-aria-invalid-no-description" in rules
    assert "forms-missing-autocomplete" in rules

    # responsive
    assert "responsive-viewport-zoom-disabled" in rules
    assert "responsive-target-size" in rules

    # visual
    assert "visual-marquee-or-blink" in rules
    assert "visual-tiny-text" in rules
    assert "visual-infinite-animation" in rules

    # screen_reader (Path A — a11y tree)
    assert "sr-duplicate-landmark" in rules


def test_dedup_collapses_overlapping_issues(sample_url):
    """axe and our own modules often flag the same element; dedup should reduce."""
    from audit.deduplicator import _key
    from audit.orchestrator import AuditOrchestrator

    result = AuditOrchestrator(
        url=sample_url,
        options={"level": "aa", "timeout_seconds": 30, "max_tabs": 30, "wait_ms": 10},
    ).run()

    # No two issues share a deduplicator key after the merge pass.
    seen: set[str] = set()
    for issue in result["issues"]:
        key = _key(issue)
        assert key not in seen, f"duplicate issue survived: {key}"
        seen.add(key)
