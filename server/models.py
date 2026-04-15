"""Pydantic request/response schemas for the audit API.

The server ignores robots.txt by design (see README). The behavior is not
exposed as a request option because it is not configurable — an opt-in flag
in the schema would misrepresent the API.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Severity(str, Enum):
    critical = "critical"
    serious = "serious"
    moderate = "moderate"
    minor = "minor"


class Viewport(BaseModel):
    width: int = 1280
    height: int = 720


class BasicAuth(BaseModel):
    username: str
    password: str


class AuditOptions(BaseModel):
    level: str = Field(default="aa", pattern="^(a|aa|aaa)$")
    modules: list[str] = Field(default_factory=lambda: ["all"])
    skip_nvda: bool | None = None  # None → use server default (platform-dependent)
    wait_ms: int = 400
    max_tabs: int = 500
    viewport: Viewport = Viewport()
    cookies: list[dict[str, Any]] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    basic_auth: BasicAuth | None = None
    timeout_seconds: int = 120


class AuditRequest(BaseModel):
    url: str
    options: AuditOptions = AuditOptions()

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return value


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
