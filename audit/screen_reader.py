"""NVDA screen reader controller.

Windows-only. On non-Windows platforms NVDAController.ensure_running raises
immediately and callers should skip the module (orchestrator does this
automatically when options.skip_nvda is True).
"""

from __future__ import annotations

import platform
from typing import Any


class NVDAUnavailableError(RuntimeError):
    pass


class NVDAController:
    """Stub implementation. A real implementation plugs into the NVDA addon
    described in the plan (nvda_addon/globalPlugins/speechCapture.py) and
    reads its speech-capture file."""

    def ensure_running(self) -> None:
        if platform.system() != "Windows":
            raise NVDAUnavailableError(
                "NVDA is only available on Windows. Set options.skip_nvda=True."
            )
        raise NotImplementedError("NVDA controller not yet implemented")

    def start_capture(self) -> None:
        raise NotImplementedError

    def stop_capture(self) -> None:
        raise NotImplementedError

    def analyze_results(self, tab_stops: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "ran": False,
            "stub": True,
            "issues": [],
            "tab_stops": len(tab_stops),
            "nvda_transcript": [],
        }

    def run_browse_mode(self, page) -> dict[str, Any]:  # noqa: ARG002
        return {"ran": False, "stub": True, "transcript": []}
