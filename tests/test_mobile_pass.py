"""Unit tests for the orchestrator's mobile-viewport pass guard.

The pass itself needs a live browser (covered by the integration run on
edaff), but the decision of *whether* to run it is pure and worth
locking down — it governs runtime cost on every audit.
"""

from __future__ import annotations

from audit.orchestrator import AuditOrchestrator


def _orch(options):
    return AuditOrchestrator(url="http://example.test", options=options)


def test_mobile_pass_on_by_default():
    assert _orch({})._should_run_mobile_pass() is True


def test_mobile_pass_can_be_disabled():
    assert _orch({"mobile_pass": False})._should_run_mobile_pass() is False


def test_mobile_pass_skipped_when_already_mobile_viewport():
    # Auditing at a phone width already — re-running the same width would
    # only yield duplicates the deduplicator discards.
    assert _orch(
        {"viewport": {"width": 390, "height": 844}}
    )._should_run_mobile_pass() is False


def test_mobile_pass_runs_at_desktop_viewport():
    assert _orch(
        {"viewport": {"width": 1440, "height": 900}}
    )._should_run_mobile_pass() is True


def test_mobile_pass_handles_malformed_viewport():
    # A bad viewport value must not crash the guard; default to running.
    assert _orch({"viewport": {"width": "wide"}})._should_run_mobile_pass() is True


def test_mobile_viewport_is_phone_width():
    assert AuditOrchestrator.MOBILE_VIEWPORT["width"] <= 600
    assert "reveal" in AuditOrchestrator.MOBILE_PASS_MODULES
