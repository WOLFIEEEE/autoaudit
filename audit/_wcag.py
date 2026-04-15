"""Centralized WCAG principle mapping.

WCAG organizes success criteria into four principles — Perceivable,
Operable, Understandable, Robust (POUR) — and every criterion number
begins with the digit of its principle (1.x, 2.x, 3.x, 4.x). The
mapping is therefore mechanical, not a lookup table.

Keeping this in one place avoids the drift that happens when every
module hand-assigns principles to its issues. The orchestrator's
scoring and the API response both depend on principles being
consistent with the WCAG criteria listed in each issue.
"""

from __future__ import annotations

_PRINCIPLE_BY_DIGIT = {
    "1": "perceivable",
    "2": "operable",
    "3": "understandable",
    "4": "robust",
}

DEFAULT_PRINCIPLE = "robust"


def principle_for(criteria: list[str]) -> str:
    """Return the WCAG principle implied by a list of success criteria.

    Uses the first criterion whose first character maps to a principle.
    Criteria like "1.4.3", "2.4.11", "4.1.2" are all handled.

    Falls back to "robust" when the list is empty or unrecognized — we
    prefer a defined default over a magic string so the scorer always
    has a bucket to count against.
    """
    for c in criteria or ():
        if c and c[0] in _PRINCIPLE_BY_DIGIT:
            return _PRINCIPLE_BY_DIGIT[c[0]]
    return DEFAULT_PRINCIPLE
