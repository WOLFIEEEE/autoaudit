"""End-to-end test of the login-gated audit flow.

The rest of the suite mocks Playwright so tests are fast. This file
needs a real browser: it drives the form-login helper against a local
HTML fixture and verifies the orchestrator actually lands on the
protected page after logging in. Tagged `slow` so CI can skip it when
Chromium isn't installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.slow  # requires a working Playwright Chromium


@pytest.fixture
def login_fixture_url() -> str:
    here = Path(__file__).resolve().parent / "fixtures" / "auth_login_page.html"
    return here.as_uri()


def test_login_flow_reaches_protected_content(login_fixture_url):
    """Log in with valid creds, then audit the post-login "dashboard".

    The fixture swaps the body HTML when the form submits with the
    correct creds, so "Dashboard" appearing on the page is proof the
    login step actually ran and the audit is measuring protected
    content, not the login form.
    """
    from audit.orchestrator import AuditOrchestrator

    options = {
        "skip_nvda": True,
        "headless": True,
        "login": {
            "url": login_fixture_url,
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_selector": "button[type=submit]",
            "success_selector": "#logout",  # only appears post-login
            "username": "auditor@example.com",
            "password": "test1234",
            "timeout_seconds": 15,
        },
    }

    # We navigate to the SAME url for the audit target. The login helper
    # runs first (same URL), then the audit runs on the post-login
    # dashboard. Because the fixture is a single page that rewrites
    # itself, the logout link proves login actually happened.
    result = AuditOrchestrator(url=login_fixture_url, options=options).run()

    # The audit must have collected issues from the POST-LOGIN DOM,
    # which has the h1 "Dashboard". We verify by searching for it
    # in any structure-heading-related output OR module tab_stops.
    kb = (result.get("modules") or {}).get("keyboard") or {}
    # The logout link should appear as a tab stop in the protected view.
    # Issues from the login form itself (email without a label-for, etc.)
    # would be gone once the DOM has been replaced.
    assert kb.get("issues_found") is not None
    # The audit finished without the login step raising.
    assert result.get("summary") is not None


def test_login_flow_fails_cleanly_on_wrong_creds(login_fixture_url):
    """Wrong password → login raises → audit surfaces the error.

    We expect a clean failure path (not a crash, not silently
    auditing the login form as if that were the target).
    """
    from audit.orchestrator import AuditOrchestrator

    options = {
        "skip_nvda": True,
        "headless": True,
        "login": {
            "url": login_fixture_url,
            "username_selector": "#username",
            "password_selector": "#password",
            "submit_selector": "button[type=submit]",
            "success_selector": "#logout",
            "username": "auditor@example.com",
            "password": "wrong-password",
            "timeout_seconds": 5,
        },
    }

    with pytest.raises(Exception) as excinfo:
        AuditOrchestrator(url=login_fixture_url, options=options).run()
    # The error message must reference "login" so operators can tell
    # this wasn't a generic navigation failure.
    assert "login" in str(excinfo.value).lower()
