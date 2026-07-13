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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictRequestModel(BaseModel):
    """Reject misspelled/unsupported request fields instead of ignoring them."""

    model_config = ConfigDict(extra="forbid")


class Severity(str, Enum):
    critical = "critical"
    serious = "serious"
    moderate = "moderate"
    minor = "minor"


class Viewport(_StrictRequestModel):
    width: int = Field(default=1280, ge=200, le=3840)
    height: int = Field(default=720, ge=200, le=2160)


class BasicAuth(_StrictRequestModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(default="", max_length=1024)


class LoginConfig(_StrictRequestModel):
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
        return validate_public_http_url(v, label="login URL")


class AttributeExpectation(_StrictRequestModel):
    selector: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=1024)


class ErrorAssociationExpectation(_StrictRequestModel):
    error_selector: str = Field(min_length=1, max_length=256)
    field_selector: str = Field(min_length=1, max_length=256)


class InteractionExpect(_StrictRequestModel):
    """Expectations checked after an interaction's trigger fires.

    Every field is optional — a single assertion is allowed to cover
    only one concern. When multiple fields are set, ALL of them must
    hold for the interaction to be reported as passing.
    """
    # After the trigger, focus must be on the element matching this
    # CSS selector. Common use: "click button → focus moves into the
    # newly opened dialog" (WCAG 2.4.3 Focus Order).
    focus_moves_to: str | None = Field(default=None, max_length=256)
    # After the trigger, this element must have the given attribute
    # value. Use for aria-expanded / aria-pressed / aria-checked
    # state round-trips (WCAG 4.1.2).
    attribute_equals: AttributeExpectation | None = None
    # Selector of an aria-live region whose text is expected to
    # change as a side effect of the trigger. Catches the classic
    # "form submitted, status text updated, but no live region"
    # bug (WCAG 4.1.3 Status Messages, level AA).
    live_region_fires: str | None = Field(default=None, max_length=256)
    # After the trigger, an element matching this selector should
    # be programmatically associated (via aria-describedby) with
    # the given form field. Used for form-error flows: submit a
    # form with invalid input, assert the error message is linked
    # to its field (WCAG 3.3.1 Error Identification, level A).
    error_describes_field: ErrorAssociationExpectation | None = None
    # When set, the trigger is expected to open a modal dialog. We
    # run a pre-baked test suite: focus moves into [role=dialog],
    # Tab/Shift+Tab cycles stay inside the dialog (focus trap),
    # and Escape closes it. Covers WCAG 2.1.2 (No Keyboard Trap),
    # 2.4.3 (Focus Order), and the common modal-implementation
    # bugs. The probe locates the dialog automatically by
    # `[role=dialog]` / `[role=alertdialog]` ancestor of focus —
    # no extra selector needed.
    modal_focus_trap: bool = False

    @model_validator(mode="after")
    def _require_expectation(self) -> "InteractionExpect":
        if not any(
            (
                self.focus_moves_to,
                self.attribute_equals,
                self.live_region_fires,
                self.error_describes_field,
                self.modal_focus_trap,
            )
        ):
            raise ValueError("at least one interaction expectation is required")
        return self


class Interaction(_StrictRequestModel):
    """One dynamic-state probe. Example:
        { name: "Open menu",
          trigger_selector: "#menu-btn", trigger_action: "click",
          expect: { focus_moves_to: "#first-item",
                    attribute_equals: {selector: "#menu-btn",
                                       name: "aria-expanded",
                                       value: "true"} } }
    """
    name: str = Field(min_length=1, max_length=120)
    trigger_selector: str = Field(min_length=1, max_length=256)
    # Small action vocabulary. Keep the DSL strict — adding new actions
    # is cheap when a real use-case appears, but accepting arbitrary
    # strings invites wedge shapes that each need their own executor.
    trigger_action: str = Field(default="click", pattern="^(click|enter|space|escape)$")
    # Milliseconds to wait after the trigger before checking expects.
    # Live regions and focus moves are typically async; 300ms is
    # enough for most SPAs without being annoying in a CI loop.
    settle_ms: int = Field(default=300, ge=0, le=10000)
    expect: InteractionExpect


class AuditOptions(_StrictRequestModel):
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
    # Dynamic-state probes. Empty by default (every audit is purely
    # static unless the caller declares a trigger/expect pair).
    interactions: list[Interaction] = Field(default_factory=list, max_length=32)
    # Caller-declared consequence class for the primary form on the
    # page. When set, we verify WCAG 3.3.4 Error Prevention applies:
    # legal/financial/data submissions must offer review, confirm,
    # or undo before the submission is finalized. Keys:
    #   "legal"       — contracts, agreements, terms acceptance
    #   "financial"   — purchases, payment authorization
    #   "data"        — medical records, tax filing, deletions
    #   "general"     — default, no 3.3.4 check applied
    form_consequence: str = Field(default="general", pattern="^(legal|financial|data|general)$")
    # Opt in to pixel-level contrast + focus-indicator analysis.
    # Off by default because it screenshots every sampled element
    # (~1-2s per element) which dominates audit time on large pages.
    pixel_analysis: bool = False
    # Embed a per-issue screenshot (as data URI) in each issue's
    # details. Off by default — adds ~0.2-0.4s per annotated issue.
    screenshots: bool = False
    # Path to a YAML rules file (team-authored DOM patterns) to run
    # alongside the built-in modules. Also overridable via env var.
    yaml_rules: str | None = Field(default=None, max_length=512)
    # Soft wall-clock budget for the whole audit, in seconds. When
    # exceeded, remaining modules are skipped with a marker in the
    # report — it doesn't kill in-flight Playwright calls because
    # the sync API doesn't support interruption.
    overall_budget_seconds: int = Field(default=0, ge=0, le=3600)
    # AI enrichment locale. "en" (default) leaves prompt English-only;
    # other values instruct the model to write enriched fields in
    # that language while keeping JSON keys / WCAG numbers in English.
    ai_language: str = Field(default="en", max_length=16)
    # Explicit opt-ins used by the orchestrator.  These were previously
    # silently discarded by Pydantic even though the implementation consumed
    # them, making the documented features unreachable through the HTTP API.
    mobile_pass: bool = True
    reveal: bool = False
    error_flow_check: bool = False
    snapshot: bool = False
    module_budget_seconds: float = Field(default=30.0, ge=0, le=600)
    vlm_checks: bool = False
    vlm_model: str | None = Field(default=None, max_length=200)
    # Used by /announce; harmless on other endpoints.
    limit: int = Field(default=500, ge=1, le=5000)
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
            if "\r" in k or "\n" in k or "\r" in v or "\n" in v:
                raise ValueError("header names/values must not contain newlines")
            if k.lower() in {
                "connection",
                "content-length",
                "host",
                "proxy-authorization",
                "proxy-connection",
                "transfer-encoding",
                "upgrade",
            }:
                raise ValueError(f"unsafe custom header is not allowed: {k}")
        return value

    @field_validator("cookies")
    @classmethod
    def _cap_cookies(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for cookie in value:
            if len(cookie) > 16:
                raise ValueError("cookie has too many fields")
            if not isinstance(cookie.get("name"), str) or not isinstance(
                cookie.get("value"), str
            ):
                raise ValueError("each cookie requires string name and value fields")
            if len(cookie["name"]) > 256 or len(cookie["value"]) > 4096:
                raise ValueError("cookie name/value too long")
            for key in ("url", "domain", "path", "partitionKey"):
                item = cookie.get(key)
                if item is not None and (not isinstance(item, str) or len(item) > 2048):
                    raise ValueError(f"cookie {key} must be a bounded string")
        return value

    @field_validator("yaml_rules")
    @classmethod
    def _check_yaml_rules_path(cls, value: str | None) -> str | None:
        # Reject obvious path-traversal at the schema layer. The
        # orchestrator additionally root-jails the path when
        # AUTOAUDIT_YAML_RULES_ROOT is set, but cheap upfront
        # rejection keeps `../etc/passwd`-style payloads off the
        # backend entirely.
        if value is None:
            return value
        v = value.strip()
        if not v:
            return None
        if "\x00" in v or any(seg == ".." for seg in v.replace("\\", "/").split("/")):
            raise ValueError("yaml_rules path must not contain '..' segments")
        root = os.environ.get("AUTOAUDIT_YAML_RULES_ROOT")
        if not root:
            raise ValueError(
                "per-request yaml_rules is disabled; configure "
                "AUTOAUDIT_YAML_RULES_ROOT first"
            )
        root_path = Path(root).resolve()
        candidate = Path(v)
        resolved = (candidate if candidate.is_absolute() else root_path / candidate).resolve()
        if not resolved.is_relative_to(root_path):
            raise ValueError("yaml_rules path escapes AUTOAUDIT_YAML_RULES_ROOT")
        return str(resolved)


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


def validate_public_http_url(value: str, *, label: str = "URL") -> str:
    if any(ord(ch) < 32 or ch.isspace() for ch in value):
        raise ValueError(f"{label} must not contain whitespace or control characters")
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"{label} must start with http:// or https://")
    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError(f"{label} is missing a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not embed credentials; use basic_auth instead")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} contains an invalid port") from exc
    if _is_blocked_host(parsed.hostname):
        raise ValueError(
            "Target resolves to a private, loopback, or reserved address; "
            "set ALLOW_PRIVATE_TARGETS=1 to override for local development"
        )
    return value


class AuditRequest(_StrictRequestModel):
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
            self.url = validate_public_http_url(self.url)
        if self.urls is not None:
            self.urls = [validate_public_http_url(u) for u in self.urls]
            if len(self.urls) == 0:
                raise ValueError("`urls` must contain at least one URL")
        return self

    def target_urls(self) -> list[str]:
        """Return the list of URLs to audit, regardless of input shape."""
        if self.urls:
            return list(self.urls)
        if self.url is None:  # defensive; model validation guarantees this
            raise RuntimeError("validated audit request has no URL")
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
