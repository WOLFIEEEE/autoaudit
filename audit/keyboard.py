"""Keyboard module: tab walking, trap detection, focus checks. STUB.

When implemented, this module will sequentially drive Tab/Shift+Tab through
the page and collect focus metadata. It cannot run in parallel with other
interactive modules since it mutates focus state.
"""

from __future__ import annotations

from typing import Any

from audit._stub import stub_result


def run(page, nvda=None, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    result = stub_result("keyboard")
    result["tab_stops"] = []
    result["traps_found"] = 0
    return result
