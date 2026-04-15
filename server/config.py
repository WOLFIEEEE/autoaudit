"""Server configuration loaded from environment variables."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass

# Absolute project root so relative defaults keep working no matter where
# the server / worker is launched from (tests change CWD).
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


@dataclass(frozen=True)
class Config:
    redis_url: str
    database_url: str
    axe_script_path: str
    axe_cdn_url: str
    default_skip_nvda: bool
    cache_ttl_seconds: int
    max_audit_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            database_url=os.environ.get("DATABASE_URL", "sqlite:///./data/audits.db"),
            axe_script_path=_abs(os.environ.get("AXE_SCRIPT_PATH", "vendor/axe.min.js")),
            axe_cdn_url=os.environ.get(
                "AXE_CDN_URL",
                "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js",
            ),
            # NVDA only runs on Windows; default to skip everywhere else.
            default_skip_nvda=os.environ.get("SKIP_NVDA", "").lower() in ("1", "true", "yes")
            or platform.system() != "Windows",
            cache_ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "900")),
            max_audit_seconds=int(os.environ.get("MAX_AUDIT_SECONDS", "180")),
        )


CONFIG = Config.from_env()
