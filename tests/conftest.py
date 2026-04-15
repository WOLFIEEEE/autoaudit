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
