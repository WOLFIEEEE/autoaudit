"""Playwright browser lifecycle manager.

Policy notes:
- Playwright does not check robots.txt. We do not add a robots.txt check.
  See README for the project's robots.txt policy.
- We set an honest, identifiable User-Agent. We do NOT use anti-bot flags
  like --disable-blink-features=AutomationControlled. This is audit tooling
  for site owners, not a scraper.
- bypass_csp=True is required to inject axe-core into pages whose CSP
  would otherwise block script-src additions.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = (
    "A11yAuditTool/0.1 (+accessibility audit tool; see project README for scope)"
)

LAUNCH_ARGS = [
    "--force-renderer-accessibility",
    "--no-first-run",
    "--disable-default-apps",
    "--disable-background-timer-throttling",
]


class BrowserManager:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def launch(self, url: str, options: dict[str, Any]):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless, args=LAUNCH_ARGS)

        vp = options.get("viewport") or {"width": 1280, "height": 720}
        context_kwargs: dict[str, Any] = {
            "viewport": vp,
            "user_agent": USER_AGENT,
            "ignore_https_errors": True,
            "bypass_csp": True,
        }

        auth = options.get("basic_auth")
        if auth and auth.get("username"):
            context_kwargs["http_credentials"] = {
                "username": auth["username"],
                "password": auth.get("password", ""),
            }

        self._context = self._browser.new_context(**context_kwargs)

        cookies = options.get("cookies") or []
        if cookies:
            self._context.add_cookies(cookies)

        headers = options.get("headers") or {}
        if headers:
            self._context.set_extra_http_headers(headers)

        self._page = self._context.new_page()
        timeout_ms = int(options.get("timeout_seconds", 30)) * 1000
        self._page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        return self._page

    @property
    def page(self):
        return self._page

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


@contextmanager
def open_page(url: str, options: dict[str, Any], headless: bool = True):
    mgr = BrowserManager(headless=headless)
    try:
        page = mgr.launch(url, options)
        yield page
    finally:
        mgr.close()
