"""Playwright browser lifecycle manager.

Policy notes:
- Playwright does not check robots.txt. We do not add a robots.txt check.
  See README for the project's robots.txt policy.
- We set an honest, identifiable User-Agent. We do NOT use anti-bot flags
  like --disable-blink-features=AutomationControlled. This is audit tooling
  for site owners, not a scraper.
- bypass_csp=True is required to inject axe-core into pages whose CSP
  would otherwise block script-src additions.

Login: when `options["login"]` is set (per LoginConfig in models.py),
the manager navigates to the login page, fills the form, and waits
for either `success_selector` or networkidle before proceeding to the
target URL. Cookies from the login persist for the lifetime of the
context and therefore apply to every page the orchestrator visits.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from server.models import validate_public_http_url

log = logging.getLogger(__name__)

# Order of browser "channels" we try when launching. Playwright's bundled
# chromium is preferred (pinned version, reproducible) but on some Windows
# setups it fails with "spawn UNKNOWN" + a side-by-side activation context
# error against its own private assembly. In that case the fallback list
# lets us land on the user's installed Edge or Chrome, which are managed
# by Windows and don't have that problem. Override with the env var
# PLAYWRIGHT_CHANNEL to pin an explicit channel (e.g. "msedge" / "chrome").
_CHANNEL_FALLBACKS: tuple[str | None, ...] = (None, "msedge", "chrome")

USER_AGENT = (
    "A11yAuditTool/0.1 (+accessibility audit tool; see project README for scope)"
)

LAUNCH_ARGS = [
    "--force-renderer-accessibility",
    "--no-first-run",
    "--disable-default-apps",
    "--disable-background-timer-throttling",
]

# Transient navigation errors we retry once. Anything else (CSP, 403,
# protocol error) is bubbled up immediately — retrying won't help.
_RETRYABLE_NAV_SUBSTRINGS = (
    "timeout",
    "net::ERR_CONNECTION_RESET",
    "net::ERR_CONNECTION_CLOSED",
    "net::ERR_NETWORK_CHANGED",
    "net::ERR_TIMED_OUT",
)


class BrowserManager:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def _launch_browser(self):
        """Launch Chromium via the first channel that succeeds.

        On Windows the bundled Chromium sometimes fails with `spawn UNKNOWN`
        because chrome.exe's SxS manifest points to a private assembly
        Windows cannot resolve at activation time (the file is there, but
        Windows rejects it — typically when Chromium is unpacked under
        %LOCALAPPDATA%). We fall back to installed Edge / Chrome in that
        case. Any other error is raised immediately: retrying a
        configuration bug with a different channel just hides it.
        """
        env_channel = os.environ.get("PLAYWRIGHT_CHANNEL")
        channels: tuple[str | None, ...] = (
            (env_channel,) if env_channel else _CHANNEL_FALLBACKS
        )
        last_exc: Exception | None = None
        for channel in channels:
            try:
                kwargs: dict[str, Any] = {"headless": self.headless, "args": LAUNCH_ARGS}
                if channel:
                    kwargs["channel"] = channel
                browser = self._pw.chromium.launch(**kwargs)
                if channel:
                    log.info("launched Chromium via channel=%s", channel)
                return browser
            except Exception as exc:
                msg = str(exc)
                is_spawn_unknown = "spawn UNKNOWN" in msg or "side-by-side" in msg
                if not is_spawn_unknown:
                    raise
                log.warning(
                    "channel=%s launch failed with SxS/spawn error; trying next",
                    channel or "bundled",
                )
                last_exc = exc
        raise RuntimeError(
            "all Chromium channels failed to launch; install Edge or Chrome, "
            "or set PLAYWRIGHT_CHANNEL explicitly"
        ) from last_exc

    def launch(self, url: str, options: dict[str, Any]):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._launch_browser()

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
            # Re-check every browser request, including redirects and
            # subresources. API-boundary validation alone can be bypassed by a
            # public URL redirecting to cloud metadata or an internal service.
            self._context.route("**/*", self._guard_request)

            cookies = options.get("cookies") or []
            if cookies:
                self._context.add_cookies(cookies)

            headers = options.get("headers") or {}
            if headers:
                self._context.set_extra_http_headers(headers)

            self._page = self._context.new_page()
            timeout_ms = int(options.get("timeout_seconds", 30)) * 1000

            # Perform form login (if configured) before hitting the
            # audit target. Login failures raise — a subsequent audit
            # of a gated page against an unauthenticated session would
            # just scan the login screen, which isn't what the user
            # asked for.
            login = options.get("login")
            if login:
                self._perform_login(login, timeout_ms)

            self._navigate_with_retry(url, timeout_ms)
            return self._page
        except Exception:
            # If we partially initialized and then crashed, tear down
            # whatever we built so we don't leak Chromium processes.
            self.close()
            raise

    @staticmethod
    def _guard_request(route, request) -> None:
        request_url = request.url
        parsed = urlparse(request_url)
        if parsed.scheme in ("http", "https"):
            try:
                validate_public_http_url(request_url)
            except ValueError as exc:
                # Log only the host; URLs may contain sensitive query strings.
                log.warning(
                    "blocked browser request to disallowed host %s: %s",
                    parsed.hostname or "<missing>",
                    exc,
                )
                route.abort("blockedbyclient")
                return
        route.continue_()

    def goto(self, url: str, timeout_ms: int | None = None):
        """Navigate the existing page to a new URL.

        Reuses the already-launched browser/context/page so cookies and
        login state persist. Used by the multi-page orchestrator.
        """
        if self._page is None:
            raise RuntimeError("BrowserManager.goto called before launch()")
        if timeout_ms is None:
            timeout_ms = 30_000
        self._navigate_with_retry(url, timeout_ms)
        return self._page

    def _perform_login(self, login: dict[str, Any], default_timeout_ms: int) -> None:
        """Fill and submit a login form in the current page.

        `login` is a dict matching LoginConfig. Each step is bounded by
        `login["timeout_seconds"]` (falling back to the overall nav
        timeout) so a stuck selector fails fast rather than consuming
        the entire audit budget.
        """
        step_timeout_ms = int(login.get("timeout_seconds", 15)) * 1000
        timeout = min(step_timeout_ms, default_timeout_ms)

        log.info("performing form login at %s", login["url"])
        self._navigate_with_retry(login["url"], timeout)

        self._page.fill(login["username_selector"], login["username"], timeout=timeout)
        self._page.fill(login["password_selector"], login["password"], timeout=timeout)

        # Click + wait in parallel: some sites navigate on submit (we want
        # to wait for the new document) and some do XHR + client-side
        # rewrite (we wait for success_selector instead).
        success_selector = login.get("success_selector")
        try:
            if success_selector:
                self._page.click(login["submit_selector"], timeout=timeout)
                self._page.wait_for_selector(success_selector, timeout=timeout)
            else:
                with self._page.expect_navigation(timeout=timeout, wait_until="networkidle"):
                    self._page.click(login["submit_selector"], timeout=timeout)
        except Exception as exc:
            raise RuntimeError(
                f"login failed: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
            ) from exc
        log.info("login successful")

    def _navigate_with_retry(self, url: str, timeout_ms: int, max_attempts: int = 2) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                self._page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                return
            except Exception as exc:
                msg = str(exc)
                retryable = any(s in msg for s in _RETRYABLE_NAV_SUBSTRINGS)
                if attempt == max_attempts or not retryable:
                    raise
                backoff = 0.5 * attempt
                log.warning(
                    "navigation to %s attempt %d failed (%s); retrying in %.1fs",
                    url,
                    attempt,
                    msg.splitlines()[0][:120],
                    backoff,
                )
                time.sleep(backoff)
                last_exc = exc
        if last_exc:
            raise last_exc

    @property
    def page(self):
        return self._page

    def close(self) -> None:
        # Teardown happens in strict reverse order. We log failures at
        # DEBUG so we don't spam production logs when the remote end
        # has already gone away, but we do record *something* — silent
        # `except: pass` has bitten us when Chromium leaked processes.
        if self._context is not None:
            try:
                self._context.close()
            except Exception as exc:
                log.debug("context.close() failed: %s", exc)
            finally:
                self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception as exc:
                log.debug("browser.close() failed: %s", exc)
            finally:
                self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception as exc:
                log.debug("playwright.stop() failed: %s", exc)
            finally:
                self._pw = None


@contextmanager
def open_page(url: str, options: dict[str, Any], headless: bool = True):
    mgr = BrowserManager(headless=headless)
    try:
        page = mgr.launch(url, options)
        yield page
    finally:
        mgr.close()


@contextmanager
def open_browser(first_url: str, options: dict[str, Any], headless: bool = True):
    """Context manager that yields the full BrowserManager so the caller
    can call `mgr.goto(next_url)` for multi-page audits without tearing
    down the context between pages.
    """
    mgr = BrowserManager(headless=headless)
    try:
        mgr.launch(first_url, options)
        yield mgr
    finally:
        mgr.close()
