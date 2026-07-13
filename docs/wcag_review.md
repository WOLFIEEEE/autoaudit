# WCAG Module Review — `audit/_wcag.py`

**Reviewer role:** WCAG 2.2 subject-matter review
**Scope:** `audit/_wcag.py` only (principle inference + SC→level table + strictest-level resolver)
**Status:** Findings + suggested patches. Nothing in this document has been applied to the codebase; it is a specification for a human to implement.

---

## 1. Summary

`audit/_wcag.py` is small, correct, and auditable. The transcribed WCAG 2.2 level table matches the W3C Recommendation. The findings below are about **robustness, downstream consumption, and drift prevention** — not correctness of the current values.

Findings are ordered from highest leverage to lowest.

---

## 2. Findings

### F1 — `4.1.1 Parsing` is obsolete in WCAG 2.2 but still returns `"A"`

**Current behavior** (`_wcag.py:142`):
```python
"4.1.1":  "A",    # Parsing (obsolete in 2.2 but kept for compat)
```

**Why it matters:** 4.1.1 was *removed* from WCAG 2.2 (not merely deprecated). Any issue still citing 4.1.1 should not contribute to an A-level failure count on a WCAG 2.2 VPAT — reporting it as `"A"` overstates non-conformance and can fail legal review.

**Suggested change:** represent obsolete SCs distinctly.

```python
# Add a sentinel alongside A/AA/AAA
_OBSOLETE = "OBSOLETE"

_WCAG_LEVELS: dict[str, str] = {
    ...
    "4.1.1":  _OBSOLETE,  # Removed in WCAG 2.2 — retained only for 2.0/2.1 back-compat
    ...
}

_LEVEL_RANK = {"A": 0, "AA": 1, "AAA": 2}  # OBSOLETE intentionally not ranked

def level_for(criteria: list[str]) -> str | None:
    best: str | None = None
    for c in criteria or ():
        lvl = _WCAG_LEVELS.get(c)
        if not lvl or lvl == _OBSOLETE:
            continue
        if best is None or _LEVEL_RANK[lvl] < _LEVEL_RANK[best]:
            best = lvl
    return best
```

Downstream (`audit/scorer.py`, VPAT export, report templates) should be audited for consumers of 4.1.1; they should drop or annotate rather than count it.

---

### F2 — `level_for`'s "strictest" semantics is easy to misuse downstream

**Current behavior** (`_wcag.py:151-170`): returns the **lowest** level in A < AA < AAA ordering, because failing A blocks all higher claims. The docstring explains this, but the name reads ambiguously — a reader glancing at the signature may expect the opposite (highest level attained).

**Suggested change:** rename and/or add an alias.

```python
def blocking_level_for(criteria: list[str]) -> str | None:
    """Lowest conformance level that would be blocked by failing any of
    these criteria. Level A blocks all claims; AA blocks AA+; AAA only AAA.
    Use this for VPAT failure reporting and traffic-light scoring."""
    ...

# Keep the old name as a thin alias for one release, then remove.
level_for = blocking_level_for
```

Callers that genuinely want "highest level present" (e.g. a tag like *"This rule touches AAA"*) should get a separate helper:

```python
def highest_level_present(criteria: list[str]) -> str | None: ...
```

---

### F3 — Input normalization

**Current behavior:** lookups are exact-string. Real-world SC strings arrive as:
- `"WCAG 1.4.3"`
- `"1.4.3 (AA)"`
- `"sc1.4.3"`
- `"1.4.03"` (zero-padded)

These all silently fall through to `None` / `DEFAULT_PRINCIPLE`, which manifests downstream as "unknown WCAG reference" bugs that are hard to trace.

**Suggested change:** add a private normalizer and call it in both public functions.

```python
import re

_SC_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

def _normalize(c: str) -> str | None:
    """Extract a canonical 'N.N.N' SC id from a messy input, or None."""
    if not c:
        return None
    m = _SC_RE.search(c)
    if not m:
        return None
    # Strip leading zeros on each segment so "1.4.03" == "1.4.3".
    return ".".join(str(int(g)) for g in m.groups())

def principle_for(criteria: list[str]) -> str:
    for raw in criteria or ():
        c = _normalize(raw)
        if c and c[0] in _PRINCIPLE_BY_DIGIT:
            return _PRINCIPLE_BY_DIGIT[c[0]]
    return DEFAULT_PRINCIPLE

def level_for(criteria: list[str]) -> str | None:
    best: str | None = None
    for raw in criteria or ():
        c = _normalize(raw)
        if not c:
            continue
        lvl = _WCAG_LEVELS.get(c)
        if not lvl or lvl == "OBSOLETE":
            continue
        if best is None or _LEVEL_RANK[lvl] < _LEVEL_RANK[best]:
            best = lvl
    return best
```

**Risk:** normalization changes observable behavior. Gate behind a unit test that confirms pre-existing clean inputs still return identical results.

---

### F4 — Drift-prevention unit tests

Without tests, the next WCAG editorial publication (or a copy-paste slip) can corrupt the table silently. The authoritative 2.2 totals are well-known and easy to assert.

**Suggested tests** (new file, e.g. `tests/test_wcag_table.py`):

```python
from collections import Counter
from audit._wcag import (
    _WCAG_LEVELS, _PRINCIPLE_BY_DIGIT, principle_for, level_for,
)

def test_level_values_are_valid():
    assert set(_WCAG_LEVELS.values()) <= {"A", "AA", "AAA", "OBSOLETE"}

def test_wcag22_level_counts():
    # Per W3C WCAG 2.2 REC: 30 A, 20 AA, 28 AAA success criteria.
    # 4.1.1 is OBSOLETE in 2.2 and excluded from the A count.
    counts = Counter(v for v in _WCAG_LEVELS.values() if v != "OBSOLETE")
    assert counts["A"] == 30
    assert counts["AA"] == 20
    assert counts["AAA"] == 28

def test_principle_digit_consistency():
    for sc in _WCAG_LEVELS:
        assert sc[0] in _PRINCIPLE_BY_DIGIT, f"unexpected principle digit: {sc}"
        assert principle_for([sc]) == _PRINCIPLE_BY_DIGIT[sc[0]]

def test_strictest_wins():
    # A beats AA beats AAA in "blocking" semantics.
    assert level_for(["1.4.3", "1.1.1"]) == "A"      # AA + A -> A
    assert level_for(["1.4.3", "1.4.6"]) == "AA"     # AA + AAA -> AA
    assert level_for(["1.4.6"]) == "AAA"

def test_unknown_returns_none():
    assert level_for([]) is None
    assert level_for(["axe-internal-rule"]) is None
    assert level_for(["4.1.1"]) is None   # obsolete

def test_new_22_criteria_present():
    for sc in ("2.4.11", "2.4.12", "2.4.13",
               "2.5.7", "2.5.8",
               "3.2.6", "3.3.7", "3.3.8", "3.3.9"):
        assert sc in _WCAG_LEVELS

def test_empty_falls_back_to_robust_principle():
    from audit._wcag import DEFAULT_PRINCIPLE
    assert principle_for([]) == DEFAULT_PRINCIPLE
    assert principle_for(["unknown"]) == DEFAULT_PRINCIPLE
```

These tests are cheap, catch the realistic failure modes (accidental level flip, missed 2.2 addition, obsolete-SC regression), and will fail loudly if the table is edited carelessly.

---

### F5 — Source-pinning the transcription

**Current comment** (`_wcag.py:49`): `Source: https://www.w3.org/TR/WCAG22/#conformance (accessed 2025-04).`

**Suggested change:** cite the dated Recommendation, not the evergreen URL, so auditors can reproduce the exact snapshot:

```
# Source: W3C Recommendation "Web Content Accessibility Guidelines (WCAG) 2.2",
# published 2023-10-05, editor's errata through 2024-12-12.
# Canonical: https://www.w3.org/TR/WCAG22/
# Versioned: https://www.w3.org/TR/2023/REC-WCAG22-20231005/
```

---

### F6 — Type narrowing

**Current signature** is `-> str | None`. Consumers that branch on the value get no help from the type checker when they forget `OBSOLETE` or mistype `"aa"`.

**Suggested change:**

```python
from typing import Literal

Level = Literal["A", "AA", "AAA"]

def level_for(criteria: list[str]) -> Level | None: ...
def principle_for(criteria: list[str]) -> Literal["perceivable", "operable", "understandable", "robust"]: ...
```

This is a pure type-annotation change; no runtime behavior shifts.

---

### F7 — EN 301 549 bridge (optional)

The module header mentions EN 301 549. EN 9.x web clauses map 1:1 onto WCAG 2.1 AA (and by extension the AA subset of 2.2 where EN has adopted it). A sibling table in a new file — e.g. `audit/_en301549.py` — would let VPAT export label issues with both the WCAG SC *and* the EN clause without muddying `_wcag.py`.

Not urgent; only worth doing when a customer actually requests an EN 301 549 section in the VPAT.

---

## 3. Suggested implementation order

1. **F4 (tests)** first — locks current behavior in before refactoring.
2. **F1 (obsolete 4.1.1)** — small, high-value correctness fix; tests from F4 will need a matching update.
3. **F3 (normalizer)** — add behind new tests for messy inputs.
4. **F2 (rename `level_for` → `blocking_level_for`)** — do this when you're willing to touch call sites.
5. **F5, F6, F7** — polish, adopt opportunistically.

## 4. Out of scope

- No changes proposed to `audit/scorer.py`, `audit/_issue.py`, VPAT export, or report templates, even though F1/F2 imply follow-up there. Treat this document as *`_wcag.py` only*; a second pass is needed to audit consumers.
- No re-transcription of the W3C table. The values in `_WCAG_LEVELS` match the 2023-10-05 Recommendation as of this review.
