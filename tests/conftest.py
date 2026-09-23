"""Shared pytest setup.

- Points Playwright at the system browsers path if not already set, so the
  e2e tests find Chromium when run under the sandbox environment.
- Keeps the rest of the test suite unaware of Playwright.
"""

from __future__ import annotations

import os

# Only set if unset — respect explicit user configuration.
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and os.path.isdir("/opt/pw-browsers"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/pw-browsers"

# The service fails closed: with no API_KEYS configured it refuses every
# non-public path unless anonymous access is an explicit choice. Almost
# every test here exercises audit behaviour rather than authentication,
# so opt the suite in by default. Auth itself is covered in
# tests/test_auth_modes.py, which sets these vars per-test.
os.environ.setdefault("ALLOW_ANONYMOUS", "1")
