"""Bind reported confidence to measured precision.

`confidence` on an issue used to be a string an author picked while
writing the rule. Nothing checked it, so a rule nobody had ever measured
could — and did — reach users marked `high`. That is how browse mode
shipped 20 `serious` false positives per page.

Here confidence becomes a claim the corpus has to support:

| Measured precision | Ceiling  |
|--------------------|----------|
| >= 0.95            | high     |
| 0.80 - 0.95        | medium   |
| < 0.80             | low      |
| never measured     | low      |

A rule's declared confidence is only ever lowered, never raised: an
author who marks a rule `medium` keeps `medium` even at precision 1.0,
because they know something the corpus does not.

The last row is the load-bearing one. A rule with no fixture cannot
reach a user as a confident finding, which makes writing the fixture the
cheapest path to shipping the rule properly — and means no future rule
can repeat the browse-mode failure.

The registry is written by `benchmarks/run_benchmark.py` on a full run
and committed, so a deployed worker knows what has evidence behind it
without carrying the corpus.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "benchmarks" / "precision.json"

# Ordered weakest to strongest so a ceiling is a simple index comparison.
_ORDER = ("low", "medium", "high")

HIGH_THRESHOLD = 0.95
MEDIUM_THRESHOLD = 0.80

_cache: dict[str, Any] | None = None


def _registry() -> dict[str, Any]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # No registry (fresh checkout, or the benchmark has never
            # run). Every rule is then unmeasured, which caps everything
            # at `low`. That is the safe direction: it understates
            # confidence rather than inventing it.
            log.warning("precision registry unavailable (%s); all rules capped at low", exc)
            _cache = {"rules": {}}
    return _cache


def reset_cache() -> None:
    """Drop the memoised registry. For tests that write their own."""
    global _cache
    _cache = None


def ceiling_for(rule: str) -> str:
    """Highest confidence the evidence supports for this rule."""
    entry = (_registry().get("rules") or {}).get(rule)
    if not entry:
        return "low"
    precision = entry.get("precision")
    if precision is None:
        return "low"
    if precision >= HIGH_THRESHOLD:
        return "high"
    if precision >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def cap(rule: str, declared: str) -> str:
    """Lower `declared` to what the corpus supports. Never raises it."""
    declared = (declared or "low").lower()
    if declared not in _ORDER:
        declared = "low"
    ceiling = ceiling_for(rule)
    return declared if _ORDER.index(declared) <= _ORDER.index(ceiling) else ceiling


def apply_to_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cap every issue's confidence in place, recording what was changed.

    A capped issue keeps its original value under `declared_confidence`
    and gains `confidence_capped: True`, so a report can explain why a
    finding is in the review queue rather than the score.
    """
    for issue in issues:
        rule = issue.get("rule") or ""
        declared = issue.get("confidence") or "high"
        effective = cap(rule, declared)
        if effective != declared:
            issue["declared_confidence"] = declared
            issue["confidence_capped"] = True
            issue["confidence"] = effective
    return issues
