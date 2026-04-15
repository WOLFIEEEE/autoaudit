"""Shared stub implementation for audit modules not yet built.

Each module exposes a `run(page, options) -> dict` returning the common
module-result shape. Stubs return an empty-but-valid result so the
orchestrator and scorer can run end-to-end.
"""

from __future__ import annotations

from typing import Any


def stub_result(module_name: str) -> dict[str, Any]:
    return {
        "ran": False,
        "stub": True,
        "module": module_name,
        "issues": [],
        "duration_seconds": 0.0,
        "note": f"{module_name} module not yet implemented",
    }
