"""Tests for the feature additions:
- Multi-page (AuditRequest.urls, AuditOrchestrator(urls=...))
- Form-login schema validation (LoginConfig)
- Preferences module rule emission
- Celery routing: audit.run_nvda routes to queue=nvda
- NVDA follow-up task logic (orchestrator + tasks layer)

We avoid booting real Playwright — all Playwright calls are mocked.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, **env: str) -> TestClient:
    db_path = tmp_path / "features.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SKIP_NVDA"] = "true"
    for k in (
        "API_KEYS",
        "RATE_LIMIT_PER_MIN",
        "LOG_FORMAT",
        "LOG_LEVEL",
        "ALLOW_PRIVATE_TARGETS",
        "MAX_AUDIT_SECONDS",
    ):
        os.environ.pop(k, None)
    os.environ.update(env)

    import importlib
    import server.app
    import server.cache
    import server.config
    import server.database
    import server.middleware
    import server.models

    importlib.reload(server.config)
    importlib.reload(server.database)
    importlib.reload(server.cache)
    importlib.reload(server.middleware)
    importlib.reload(server.models)
    importlib.reload(server.app)

    return TestClient(server.app.create_app())


# ---------------------------------------------------------------
# Multi-URL validation / orchestration


def test_audit_request_accepts_urls_list(tmp_path):
    with _fresh_app(tmp_path) as c:
        with patch("server.cache.get_cached_result", return_value=None), \
             patch("server.tasks.run_audit_task.delay", return_value=None):
            resp = c.post(
                "/audit",
                json={"urls": ["https://example.com/", "https://example.org/"]},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # estimated_seconds scales with number of URLs.
            assert body["estimated_seconds"] >= 60 * 2


def test_audit_request_rejects_both_url_and_urls(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={
                "url": "https://example.com/",
                "urls": ["https://example.org/"],
            },
        )
        assert resp.status_code == 422
        assert "not both" in resp.text.lower() or "either" in resp.text.lower()


def test_audit_request_rejects_empty_urls(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post("/audit", json={"urls": []})
        assert resp.status_code == 422


def test_audit_request_caps_urls_length(tmp_path):
    with _fresh_app(tmp_path) as c:
        urls = [f"https://example.com/{i}" for i in range(26)]  # cap is 25
        resp = c.post("/audit", json={"urls": urls})
        assert resp.status_code == 422


def test_audit_request_each_url_ssrf_checked(tmp_path):
    with _fresh_app(tmp_path) as c:
        resp = c.post(
            "/audit",
            json={"urls": ["https://example.com/", "http://127.0.0.1/"]},
        )
        assert resp.status_code == 422


def test_orchestrator_accepts_urls_kwarg():
    from audit.orchestrator import AuditOrchestrator

    o = AuditOrchestrator(urls=["https://a.example/", "https://b.example/"], options={})
    assert o.urls == ["https://a.example/", "https://b.example/"]
    assert o.url == "https://a.example/"


def test_orchestrator_rejects_neither_or_both_urls():
    from audit.orchestrator import AuditOrchestrator

    with pytest.raises(ValueError):
        AuditOrchestrator(options={})  # neither url nor urls
    with pytest.raises(ValueError):
        AuditOrchestrator(url="https://a.example/", urls=["https://b.example/"], options={})


# ---------------------------------------------------------------
# LoginConfig


def test_login_config_schema(tmp_path):
    """AuditOptions accepts a login config; validation enforces scheme."""
    from server.models import AuditOptions, LoginConfig

    opts = AuditOptions(
        login=LoginConfig(
            url="https://example.com/login",
            username_selector="#email",
            password_selector="#pw",
            submit_selector="button[type=submit]",
            username="alice",
            password="s3cret",
            success_selector=".logout",
        )
    )
    assert opts.login is not None
    assert opts.login.username == "alice"


def test_login_config_rejects_non_http_url():
    from pydantic import ValidationError

    from server.models import LoginConfig

    with pytest.raises(ValidationError):
        LoginConfig(
            url="javascript:alert(1)",
            username_selector="#u",
            password_selector="#p",
            submit_selector="#s",
            username="a",
            password="b",
        )


# ---------------------------------------------------------------
# Preferences module


def test_preferences_flags_missing_rmm_and_forced_colors_queries():
    from audit.preferences import run

    page = MagicMock()
    page.evaluate.return_value = {
        "hasReducedMotionQuery": False,
        "hasForcedColorsQuery": False,
        "stillAnimating": [],
    }
    result = run(page, {})
    assert result["ran"] is True
    rules = {i["rule"] for i in result["issues"]}
    assert "preferences-no-reduced-motion-query" in rules
    assert "preferences-no-forced-colors-query" in rules


def test_preferences_flags_still_animating_under_reduced_motion():
    from audit.preferences import run

    page = MagicMock()
    page.evaluate.return_value = {
        "hasReducedMotionQuery": True,
        "hasForcedColorsQuery": True,
        "stillAnimating": [
            {
                "tag": "div",
                "id": "hero",
                "cls": "hero-animated",
                "animation": "spin",
                "duration": "2s",
                "iterations": "infinite",
            }
        ],
    }
    result = run(page, {})
    assert result["ran"] is True
    rules = {i["rule"] for i in result["issues"]}
    assert "preferences-reduced-motion-ignored" in rules
    # Only one of the two "missing query" rules should NOT be present (both stylesheets satisfy).
    assert "preferences-no-reduced-motion-query" not in rules
    assert "preferences-no-forced-colors-query" not in rules


def test_preferences_handles_evaluate_failure():
    from audit.preferences import run

    page = MagicMock()
    page.emulate_media = MagicMock()
    page.evaluate.side_effect = RuntimeError("page closed")
    result = run(page, {})
    assert result["ran"] is False
    assert "page closed" in result["error"]
    # Resets emulate_media even on failure.
    assert any(
        call.kwargs.get("reduced_motion") == "no-preference"
        for call in page.emulate_media.call_args_list
    )


def test_preferences_resets_emulate_media_on_success():
    from audit.preferences import run

    page = MagicMock()
    page.evaluate.return_value = {
        "hasReducedMotionQuery": True,
        "hasForcedColorsQuery": True,
        "stillAnimating": [],
    }
    run(page, {})
    calls = [c for c in page.emulate_media.call_args_list]
    kwargs = [c.kwargs.get("reduced_motion") for c in calls]
    assert "reduce" in kwargs
    assert "no-preference" in kwargs


# ---------------------------------------------------------------
# Celery routing


def test_nvda_task_routes_to_nvda_queue():
    from celery_app import celery_app

    routes = celery_app.conf.task_routes or {}
    assert "audit.run_nvda" in routes
    assert routes["audit.run_nvda"]["queue"] == "nvda"


def test_default_queue_is_default():
    from celery_app import celery_app

    assert celery_app.conf.task_default_queue == "default"


# ---------------------------------------------------------------
# NVDA follow-up — platform gating


def test_nvda_follow_up_skips_on_non_windows():
    """On a Linux/Mac host the follow-up helper returns a skipped patch
    without touching Playwright — that's what makes misrouting safe."""
    from audit.orchestrator import run_nvda_follow_up

    # We don't need to stub platform: this test runs on Darwin/Linux.
    result = run_nvda_follow_up("https://example.com/", {"skip_nvda": False})
    assert result["nvda_status"] == "skipped"
    assert result["nvda"]["ran"] is False
    assert "non-Windows" in result["nvda"]["reason"]


# ---------------------------------------------------------------
# run_nvda_task integration (mocked orchestration)


def test_run_nvda_task_merges_into_existing_audit(tmp_path):
    """The follow-up task should append NVDA issues and rewrite summary."""
    from importlib import reload

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/nvda.db"
    import server.config as sc
    import server.database as sd
    reload(sc)
    reload(sd)
    sd.init_db()

    # Seed a completed audit row.
    job_id = "job-nvda-1"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sd.create_job(job_id, "https://example.com/", now)
    initial_result = {
        "url": "https://example.com/",
        "timestamp": now,
        "duration_seconds": 1.0,
        "summary": {
            "score": 90,
            "grade": "A",
            "total_issues": 1,
            "by_severity": {"critical": 0, "serious": 1, "moderate": 0, "minor": 0},
            "by_principle": {},
        },
        "issues": [
            {
                "id": "base-1",
                "module": "structure",
                "rule": "structure-html-lang",
                "severity": "serious",
                "principle": "understandable",
                "wcag_criteria": ["3.1.1"],
                "title": "missing lang",
                "description": "",
                "element": {},
                "details": {},
                "fix_suggestion": "",
            }
        ],
        "modules": {
            "structure": {"ran": True, "issues_found": 1, "duration_seconds": 0.1, "error": None},
            "screen_reader": {"ran": True, "issues_found": 0, "duration_seconds": 0.1, "error": None},
        },
        "nvda_status": "pending",
    }
    sd.save_job_result(job_id, initial_result, now)

    # Have the follow-up "succeed" with a new issue.
    fake_patch = {
        "nvda_status": "completed",
        "nvda": {"ran": True, "issues": [], "transcript": ["something"]},
        "issues": [
            {
                "id": "nvda-1",
                "module": "screen_reader",
                "rule": "sr-silent-interactive",
                "severity": "critical",
                "principle": "robust",
                "wcag_criteria": ["4.1.2"],
                "title": "button has no name",
                "description": "",
                "element": {},
                "details": {},
                "fix_suggestion": "",
            }
        ],
        "duration_seconds": 1.2,
    }

    with patch("audit.orchestrator.run_nvda_follow_up", return_value=fake_patch), \
         patch("server.cache.set_cached_result"):
        # Reload tasks so it picks up the fresh DATABASE_URL.
        import server.tasks as st
        reload(st)
        # Call the task function directly (synchronous path).
        result = st.run_nvda_task.run(job_id=job_id, url="https://example.com/", options={})

    assert result["nvda_status"] == "completed"

    updated = sd.get_audit_result(job_id)
    assert updated is not None
    assert updated["nvda_status"] == "completed"
    # Both original and NVDA issues present.
    rules = {i["rule"] for i in updated["issues"]}
    assert "structure-html-lang" in rules
    assert "sr-silent-interactive" in rules
    # Summary was recomputed.
    assert updated["summary"]["total_issues"] == 2


def test_run_nvda_task_handles_missing_parent(tmp_path):
    from importlib import reload

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/nvda_missing.db"
    import server.config as sc
    import server.database as sd
    reload(sc)
    reload(sd)
    sd.init_db()

    import server.tasks as st
    reload(st)

    result = st.run_nvda_task.run(
        job_id="does-not-exist", url="https://example.com/", options={}
    )
    assert result["status"] == "skipped"
    assert "no parent audit" in result["reason"]
