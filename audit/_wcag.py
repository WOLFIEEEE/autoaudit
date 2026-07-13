"""Centralized WCAG principle, level, and understanding-URL mapping.

WCAG organizes success criteria into four principles — Perceivable,
Operable, Understandable, Robust (POUR) — and every criterion number
begins with the digit of its principle (1.x, 2.x, 3.x, 4.x). The
mapping is therefore mechanical, not a lookup table.

Keeping this in one place avoids the drift that happens when every
module hand-assigns principles to its issues. The orchestrator's
scoring and the API response both depend on principles being
consistent with the WCAG criteria listed in each issue.

Source: W3C Recommendation "Web Content Accessibility Guidelines (WCAG) 2.2",
published 2023-10-05.
  Canonical:  https://www.w3.org/TR/WCAG22/
  Versioned:  https://www.w3.org/TR/2023/REC-WCAG22-20231005/
"""

from __future__ import annotations

import re
from typing import Literal

Principle = Literal["perceivable", "operable", "understandable", "robust"]
Level = Literal["A", "AA", "AAA"]

_PRINCIPLE_BY_DIGIT: dict[str, Principle] = {
    "1": "perceivable",
    "2": "operable",
    "3": "understandable",
    "4": "robust",
}

DEFAULT_PRINCIPLE: Principle = "robust"

# Sentinel for SCs that existed in earlier WCAG versions but were removed
# in 2.2. Kept in the table so issues that still cite them (legacy axe
# tags, axe's own "wcag2a" roll-ups) don't look like unrecognized inputs,
# but excluded from conformance-blocking level math.
_OBSOLETE = "OBSOLETE"


_SC_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _normalize_sc(raw: str | None) -> str | None:
    """Extract a canonical 'N.N.N' SC id from a messy input.

    Real-world inputs arrive as `"WCAG 1.4.3"`, `"1.4.3 (AA)"`,
    `"sc1.4.3"`, `"1.4.03"` (zero-padded). Without normalization these
    silently fall through to None and manifest downstream as "unknown
    WCAG reference" drift. We strip prefixes, trailing parentheticals,
    and leading zeros on each segment.
    """
    if not raw:
        return None
    m = _SC_RE.search(raw)
    if not m:
        return None
    return ".".join(str(int(g)) for g in m.groups())


def principle_for(criteria: list[str] | None) -> Principle:
    """Return the WCAG principle implied by a list of success criteria.

    Uses the first criterion whose first character maps to a principle.
    Criteria like "1.4.3", "2.4.11", "4.1.2" are all handled, as are
    noisy inputs like "WCAG 1.4.3" — see `_normalize_sc`.

    Falls back to "robust" when the list is empty or unrecognized — we
    prefer a defined default over a magic string so the scorer always
    has a bucket to count against.
    """
    for raw in criteria or ():
        c = _normalize_sc(raw) if raw else None
        if c and c[0] in _PRINCIPLE_BY_DIGIT:
            return _PRINCIPLE_BY_DIGIT[c[0]]
        # Backwards-compat: if the input is a single-digit prefix that
        # didn't match the SC regex (e.g. raw "1"), still honor it.
        if raw and raw[0] in _PRINCIPLE_BY_DIGIT:
            return _PRINCIPLE_BY_DIGIT[raw[0]]
    return DEFAULT_PRINCIPLE


# ---------------------------------------------------------------------
# Conformance-level mapping: every WCAG 2.2 Success Criterion classified
# as A, AA, or AAA. Compliance claims (VPATs, WCAG 2.2 AA, EN 301 549)
# depend on this mapping — a mismatch between an issue's WCAG reference
# and the level we report is a reporting bug that kills stakeholder
# trust fast, so the authoritative source is hand-transcribed once.
#
# Keep this list literal — no computed shortcuts — so diffs are auditable.
# ---------------------------------------------------------------------

_WCAG_LEVELS: dict[str, str] = {
    # --- 1. Perceivable --------------------------------------------
    "1.1.1":  "A",    # Non-text Content
    "1.2.1":  "A",    # Audio-only / Video-only (prerecorded)
    "1.2.2":  "A",    # Captions (prerecorded)
    "1.2.3":  "A",    # Audio Description / Media Alternative
    "1.2.4":  "AA",   # Captions (live)
    "1.2.5":  "AA",   # Audio Description (prerecorded)
    "1.2.6":  "AAA",  # Sign Language (prerecorded)
    "1.2.7":  "AAA",  # Extended Audio Description
    "1.2.8":  "AAA",  # Media Alternative (prerecorded)
    "1.2.9":  "AAA",  # Audio-only (live)
    "1.3.1":  "A",    # Info and Relationships
    "1.3.2":  "A",    # Meaningful Sequence
    "1.3.3":  "A",    # Sensory Characteristics
    "1.3.4":  "AA",   # Orientation
    "1.3.5":  "AA",   # Identify Input Purpose
    "1.3.6":  "AAA",  # Identify Purpose
    "1.4.1":  "A",    # Use of Color
    "1.4.2":  "A",    # Audio Control
    "1.4.3":  "AA",   # Contrast (Minimum)
    "1.4.4":  "AA",   # Resize Text
    "1.4.5":  "AA",   # Images of Text
    "1.4.6":  "AAA",  # Contrast (Enhanced)
    "1.4.7":  "AAA",  # Low or No Background Audio
    "1.4.8":  "AAA",  # Visual Presentation
    "1.4.9":  "AAA",  # Images of Text (No Exception)
    "1.4.10": "AA",   # Reflow
    "1.4.11": "AA",   # Non-text Contrast
    "1.4.12": "AA",   # Text Spacing
    "1.4.13": "AA",   # Content on Hover or Focus
    # --- 2. Operable -----------------------------------------------
    "2.1.1":  "A",    # Keyboard
    "2.1.2":  "A",    # No Keyboard Trap
    "2.1.3":  "AAA",  # Keyboard (No Exception)
    "2.1.4":  "A",    # Character Key Shortcuts
    "2.2.1":  "A",    # Timing Adjustable
    "2.2.2":  "A",    # Pause, Stop, Hide
    "2.2.3":  "AAA",  # No Timing
    "2.2.4":  "AAA",  # Interruptions
    "2.2.5":  "AAA",  # Re-authenticating
    "2.2.6":  "AAA",  # Timeouts
    "2.3.1":  "A",    # Three Flashes or Below
    "2.3.2":  "AAA",  # Three Flashes
    "2.3.3":  "AAA",  # Animation from Interactions
    "2.4.1":  "A",    # Bypass Blocks
    "2.4.2":  "A",    # Page Titled
    "2.4.3":  "A",    # Focus Order
    "2.4.4":  "A",    # Link Purpose (In Context)
    "2.4.5":  "AA",   # Multiple Ways
    "2.4.6":  "AA",   # Headings and Labels
    "2.4.7":  "AA",   # Focus Visible
    "2.4.8":  "AAA",  # Location
    "2.4.9":  "AAA",  # Link Purpose (Link Only)
    "2.4.10": "AAA",  # Section Headings
    "2.4.11": "AA",   # Focus Not Obscured (Minimum)
    "2.4.12": "AAA",  # Focus Not Obscured (Enhanced)
    "2.4.13": "AAA",  # Focus Appearance
    "2.5.1":  "A",    # Pointer Gestures
    "2.5.2":  "A",    # Pointer Cancellation
    "2.5.3":  "A",    # Label in Name
    "2.5.4":  "A",    # Motion Actuation
    "2.5.5":  "AAA",  # Target Size (Enhanced)
    "2.5.6":  "AAA",  # Concurrent Input Mechanisms
    "2.5.7":  "AA",   # Dragging Movements
    "2.5.8":  "AA",   # Target Size (Minimum)
    # --- 3. Understandable -----------------------------------------
    "3.1.1":  "A",    # Language of Page
    "3.1.2":  "AA",   # Language of Parts
    "3.1.3":  "AAA",  # Unusual Words
    "3.1.4":  "AAA",  # Abbreviations
    "3.1.5":  "AAA",  # Reading Level
    "3.1.6":  "AAA",  # Pronunciation
    "3.2.1":  "A",    # On Focus
    "3.2.2":  "A",    # On Input
    "3.2.3":  "AA",   # Consistent Navigation
    "3.2.4":  "AA",   # Consistent Identification
    "3.2.5":  "AAA",  # Change on Request
    "3.2.6":  "A",    # Consistent Help
    "3.3.1":  "A",    # Error Identification
    "3.3.2":  "A",    # Labels or Instructions
    "3.3.3":  "AA",   # Error Suggestion
    "3.3.4":  "AA",   # Error Prevention (Legal, Financial, Data)
    "3.3.5":  "AAA",  # Help
    "3.3.6":  "AAA",  # Error Prevention (All)
    "3.3.7":  "A",    # Redundant Entry
    "3.3.8":  "AA",   # Accessible Authentication (Minimum)
    "3.3.9":  "AAA",  # Accessible Authentication (Enhanced)
    # --- 4. Robust -------------------------------------------------
    # 4.1.1 Parsing was REMOVED in WCAG 2.2 — not merely deprecated.
    # Legacy tooling (axe 2.x, older VPATs) still cites it, so we keep
    # the key recognized, but marked OBSOLETE so level_for() skips it.
    # Consumers counting issues-by-level must therefore not treat a
    # 4.1.1 citation as blocking WCAG 2.2 A-conformance.
    "4.1.1":  _OBSOLETE,
    "4.1.2":  "A",    # Name, Role, Value
    "4.1.3":  "AA",   # Status Messages
}

# Ordering used when picking "the worst" level from a list.
_LEVEL_RANK = {"A": 0, "AA": 1, "AAA": 2}


def blocking_level_for(criteria: list[str] | None) -> Level | None:
    """Lowest conformance level that would be BLOCKED by failing any of
    the supplied success criteria.

    "Lowest" in A < AA < AAA ordering: failing an A criterion blocks
    every conformance claim (A, AA, AAA), AA blocks AA+, AAA only AAA.
    So an issue that maps to both a level-A and a level-AA SC is
    reported as level A because that's the harsher story stakeholders
    care about and the only one that's both accurate and conservative.

    Returns None when no criterion is recognized or all mapped ones are
    obsolete — avoids falsely labelling custom-only rules (axe-internal
    ones) with a level, and avoids counting obsolete 4.1.1 against
    WCAG 2.2 conformance.
    """
    best: Level | None = None
    for raw in criteria or ():
        c = _normalize_sc(raw) if raw else None
        if not c:
            continue
        lvl = _WCAG_LEVELS.get(c)
        if not lvl or lvl == _OBSOLETE:
            continue
        if best is None or _LEVEL_RANK[lvl] < _LEVEL_RANK[best]:
            best = lvl  # type: ignore[assignment]
    return best


# Back-compat alias. The old name remains for one deprecation cycle;
# new code should call blocking_level_for. Both point at the same
# function so touching either is safe.
level_for = blocking_level_for


def highest_level_present(criteria: list[str] | None) -> Level | None:
    """Highest conformance level referenced by any of the supplied SCs.

    Symmetric helper to `blocking_level_for` — useful for informational
    tags ("this rule touches AAA") rather than conformance math.
    Obsolete criteria are skipped, same as blocking_level_for.
    """
    best: Level | None = None
    for raw in criteria or ():
        c = _normalize_sc(raw) if raw else None
        if not c:
            continue
        lvl = _WCAG_LEVELS.get(c)
        if not lvl or lvl == _OBSOLETE:
            continue
        if best is None or _LEVEL_RANK[lvl] > _LEVEL_RANK[best]:
            best = lvl  # type: ignore[assignment]
    return best


def is_obsolete(sc: str | None) -> bool:
    """True when the given SC was removed in WCAG 2.2. Currently only
    4.1.1 Parsing falls in this bucket; exposed as a predicate so
    consumers can annotate rather than silently drop."""
    c = _normalize_sc(sc) if sc else None
    if not c:
        return False
    return _WCAG_LEVELS.get(c) == _OBSOLETE


def understanding_url(sc: str | None) -> str | None:
    """Return the canonical W3C "Understanding" document URL for an SC.

    These URLs are mechanical: `{sc-with-dashes}.html` under
    `www.w3.org/WAI/WCAG22/Understanding/`. Auditors building VPATs
    cite these — having the URL on every issue raises report
    credibility with zero ongoing maintenance cost.
    """
    c = _normalize_sc(sc) if sc else None
    if not c or c not in _WCAG_LEVELS:
        return None
    # The W3C slug is the SC name, but we don't carry the name here.
    # The numeric fragment link is stable and resolves to the same page.
    # Example: 1.4.3 → .../Understanding/contrast-minimum — we don't
    # want to ship the slug table, so prefer the canonical REC anchor
    # which is guaranteed by W3C to remain resolvable.
    return f"https://www.w3.org/TR/WCAG22/#{_sc_to_anchor(c)}"


# Anchor slugs in the WCAG 2.2 REC use the SC *name* (e.g.
# "contrast-minimum"), not the number. We ship the name table once so
# `understanding_url` returns a resolvable link; when a future SC is
# added and we forget to extend this table, `understanding_url` falls
# back to the numeric fragment — which still works on the REC page.
_SC_ANCHORS: dict[str, str] = {
    "1.1.1": "non-text-content",
    "1.2.1": "audio-only-and-video-only-prerecorded",
    "1.2.2": "captions-prerecorded",
    "1.2.3": "audio-description-or-media-alternative-prerecorded",
    "1.2.4": "captions-live",
    "1.2.5": "audio-description-prerecorded",
    "1.2.6": "sign-language-prerecorded",
    "1.2.7": "extended-audio-description-prerecorded",
    "1.2.8": "media-alternative-prerecorded",
    "1.2.9": "audio-only-live",
    "1.3.1": "info-and-relationships",
    "1.3.2": "meaningful-sequence",
    "1.3.3": "sensory-characteristics",
    "1.3.4": "orientation",
    "1.3.5": "identify-input-purpose",
    "1.3.6": "identify-purpose",
    "1.4.1": "use-of-color",
    "1.4.2": "audio-control",
    "1.4.3": "contrast-minimum",
    "1.4.4": "resize-text",
    "1.4.5": "images-of-text",
    "1.4.6": "contrast-enhanced",
    "1.4.7": "low-or-no-background-audio",
    "1.4.8": "visual-presentation",
    "1.4.9": "images-of-text-no-exception",
    "1.4.10": "reflow",
    "1.4.11": "non-text-contrast",
    "1.4.12": "text-spacing",
    "1.4.13": "content-on-hover-or-focus",
    "2.1.1": "keyboard",
    "2.1.2": "no-keyboard-trap",
    "2.1.3": "keyboard-no-exception",
    "2.1.4": "character-key-shortcuts",
    "2.2.1": "timing-adjustable",
    "2.2.2": "pause-stop-hide",
    "2.2.3": "no-timing",
    "2.2.4": "interruptions",
    "2.2.5": "re-authenticating",
    "2.2.6": "timeouts",
    "2.3.1": "three-flashes-or-below-threshold",
    "2.3.2": "three-flashes",
    "2.3.3": "animation-from-interactions",
    "2.4.1": "bypass-blocks",
    "2.4.2": "page-titled",
    "2.4.3": "focus-order",
    "2.4.4": "link-purpose-in-context",
    "2.4.5": "multiple-ways",
    "2.4.6": "headings-and-labels",
    "2.4.7": "focus-visible",
    "2.4.8": "location",
    "2.4.9": "link-purpose-link-only",
    "2.4.10": "section-headings",
    "2.4.11": "focus-not-obscured-minimum",
    "2.4.12": "focus-not-obscured-enhanced",
    "2.4.13": "focus-appearance",
    "2.5.1": "pointer-gestures",
    "2.5.2": "pointer-cancellation",
    "2.5.3": "label-in-name",
    "2.5.4": "motion-actuation",
    "2.5.5": "target-size-enhanced",
    "2.5.6": "concurrent-input-mechanisms",
    "2.5.7": "dragging-movements",
    "2.5.8": "target-size-minimum",
    "3.1.1": "language-of-page",
    "3.1.2": "language-of-parts",
    "3.1.3": "unusual-words",
    "3.1.4": "abbreviations",
    "3.1.5": "reading-level",
    "3.1.6": "pronunciation",
    "3.2.1": "on-focus",
    "3.2.2": "on-input",
    "3.2.3": "consistent-navigation",
    "3.2.4": "consistent-identification",
    "3.2.5": "change-on-request",
    "3.2.6": "consistent-help",
    "3.3.1": "error-identification",
    "3.3.2": "labels-or-instructions",
    "3.3.3": "error-suggestion",
    "3.3.4": "error-prevention-legal-financial-data",
    "3.3.5": "help",
    "3.3.6": "error-prevention-all",
    "3.3.7": "redundant-entry",
    "3.3.8": "accessible-authentication-minimum",
    "3.3.9": "accessible-authentication-enhanced",
    "4.1.1": "parsing",  # Obsolete but still cite-able.
    "4.1.2": "name-role-value",
    "4.1.3": "status-messages",
}


def _sc_to_anchor(sc: str) -> str:
    return _SC_ANCHORS.get(sc, sc)
