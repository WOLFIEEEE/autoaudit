"""Pydantic request/response schemas for the audit API.

The server ignores robots.txt by design (see README). The behavior is not
exposed as a request option because it is not configurable — an opt-in flag
in the schema would misrepresent the API.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class Severity(str, Enum):
    critical = "critical"
    serious = "serious"
    moderate = "moderate"
    minor = "minor"


class Viewport(BaseModel):
    width: int = Field(default=1280, ge=200, le=3840)
    height: int = Field(default=720, ge=200, le=2160)


class BasicAuth(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(default="", max_length=1024)


class LoginConfig(BaseModel):
    """Form-based login performed once before the audit navigates.

    Applies to a single browser context: after the login submit, the
    resulting cookies persist for every page visited in the audit
    (including multi-page crawls). Use `basic_auth` instead when the
    site uses HTTP auth.
    """

    url: str = Field(min_length=1, max_length=2048)
    username_selector: str = Field(min_length=1, max_length=256)
    password_selector: str = Field(min_length=1, max_length=256)
    submit_selector: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(max_length=1024)
    # Optional: a selector that must be visible after login to consider
    # it successful (e.g. a "Logout" link, an account menu). When unset
    # we fall back to waiting for networkidle.
    success_selector: str | None = Field(default=None, max_length=256)
    # Per-step timeout. Caps login end-to-end at a few multiples of this.
    timeout_seconds: int = Field(default=15, ge=1, le=120)

    @field_validator("url")
    @classmethod
    def _check_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("login URL must start with http:// or https://")
        return v


class AuditOptions(BaseModel):
    level: str = Field(default="aa", pattern="^(a|aa|aaa)$")
    modules: list[str] = Field(default_factory=lambda: ["all"], max_length=32)
    skip_nvda: bool | None = None  # None → use server default (platform-dependent)
    wait_ms: int = Field(default=400, ge=0, le=30000)
    max_tabs: int = Field(default=500, ge=0, le=5000)
    viewport: Viewport = Viewport()
    cookies: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    headers: dict[str, str] = Field(default_factory=dict)
    basic_auth: BasicAuth | None = None
    login: LoginConfig | None = None
    # Cap timeout_seconds at 600 (10 minutes). Anything longer indicates
    # misuse — Celery soft-timeout is 180s by default, so values above that
    # won't help anyway.
    timeout_seconds: int = Field(default=120, ge=1, le=600)

    @field_validator("headers")
    @classmethod
    def _cap_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 50:
            raise ValueError("too many custom headers (max 50)")
        for k, v in value.items():
            if len(k) > 256 or len(v) > 4096:
                raise ValueError("header name/value too long")
        return value


def _is_blocked_host(hostname: str) -> bool:
    """Return True if hostname resolves to a private / loopback / reserved address.

    Controlled by env var ALLOW_PRIVATE_TARGETS=1 so the tests (and local
    development against localhost) can opt out. Production should leave it
    unset.
    """
    if os.environ.get("ALLOW_PRIVATE_TARGETS", "").lower() in ("1", "true", "yes"):
        return False
    if not hostname:
        return True
    # First, is it already a numeric IP?
    try:
        ip = ipaddress.ip_address(hostname)
        return _ip_is_blocked(ip)
    except ValueError:
        pass
    # Otherwise resolve. We block if *any* resolved address is private —
    # DNS rebinding is still possible at fetch time, but this rejects the
    # obvious cases (localhost, internal hostnames).
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        # Unresolvable — let Playwright surface the error at navigation time
        # rather than 422ing here; the user may be running behind a split
        # DNS or proxy.
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_is_blocked(ip):
            return True
    return False


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_public_http_url(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError("URL is missing a hostname")
    if _is_blocked_host(parsed.hostname):
        raise ValueError(
            "Target resolves to a private, loopback, or reserved address; "
            "set ALLOW_PRIVATE_TARGETS=1 to override for local development"
        )
    return value


class AuditRequest(BaseModel):
    """A single-URL or multi-URL audit request.

    Exactly one of `url` or `urls` must be provided. Multi-URL requests
    audit each page in sequence, in the same browser context (cookies
    and login persist across pages), and aggregate the results into a
    top-level summary.
    """

    url: str | None = Field(default=None, max_length=2048)
    urls: list[str] | None = Field(default=None, max_length=25)
    options: AuditOptions = AuditOptions()

    @model_validator(mode="after")
    def _check_exactly_one_url_source(self) -> "AuditRequest":
        has_url = self.url is not None and self.url != ""
        has_urls = bool(self.urls)
        if has_url and has_urls:
            raise ValueError("provide either `url` or `urls`, not both")
        if not has_url and not has_urls:
            raise ValueError("one of `url` or `urls` is required")
        if self.url is not None:
            self.url = _validate_public_http_url(self.url)
        if self.urls is not None:
            self.urls = [_validate_public_http_url(u) for u in self.urls]
            if len(self.urls) == 0:
                raise ValueError("`urls` must contain at least one URL")
        return self

    def target_urls(self) -> list[str]:
        """Return the list of URLs to audit, regardless of input shape."""
        if self.urls:
            return list(self.urls)
        assert self.url is not None
        return [self.url]


class Issue(BaseModel):
    id: str
    module: str
    rule: str
    severity: Severity
    principle: str
    wcag_criteria: list[str] = Field(default_factory=list)
    title: str
    description: str = ""
    element: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    fix_suggestion: str = ""


class AuditStatus(BaseModel):
    job_id: str
    status: str
    estimated_seconds: int | None = None
    poll_url: str


class PrincipleScore(BaseModel):
    score: int
    issues: int


class Summary(BaseModel):
    score: int
    grade: str
    total_issues: int
    by_severity: dict[str, int]
    by_principle: dict[str, PrincipleScore]


class ModuleSummary(BaseModel):
    ran: bool
    issues_found: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


class AuditResponse(BaseModel):
    job_id: str
    status: str
    url: str
    timestamp: str
    duration_seconds: float
    summary: Summary
    issues: list[Issue]
    modules: dict[str, ModuleSummary]
