# Automation roadmap — getting from ~38% to ~67% SC coverage

This document is the plan of record for raising the share of WCAG 2.2
A/AA Success Criteria (SCs) that this audit covers **without human
review of every page**. It is a *detection* roadmap only: every item
below is about finding and reporting barriers more completely. Nothing
here generates fixes, patches, or PRs — remediation is out of scope for
this product.

## The metric (read this first)

There are two different denominators and they must never be conflated:

- **SC coverage %** — of the 55 A/AA criteria, how many do we
  *meaningfully test*? This is the number this roadmap moves and the
  number we may publish, **always alongside the caveat below**.
- **Real-barrier catch %** — of all barriers on a real site, weighted
  by how often they occur, how many do we flag? This is the famous
  "automated tools catch ~35%" figure. It is a stricter denominator and
  we do **not** claim to move it to 67%.

A perfect SC-coverage number is still **not** a conformance claim. It
means our automated + AI-assisted rules found nothing; "Not Evaluated"
and manual-review rows in the VPAT show where work remains.

## The four-tier model

`audit/wcag_coverage.py` is the single source of truth. Each A/AA SC
sits in exactly one tier:

| Tier | Meaning | Counts toward "covered"? |
|---|---|---|
| `automated` | Deterministic rule(s) flag common failures with high confidence (attribute present/absent, DOM relationship, geometry). | Yes |
| `ai_assisted` | A vision/LLM judgement makes the call (alt-text usefulness, error clarity). Reported with confidence and **always flagged for human confirmation** — never a silent pass. | Yes (with caveat) |
| `partial` | Automation catches *some* failure modes; the SC fundamentally needs human judgement for the rest. | Half-credit |
| `manual_only` | Out of scope for automation from a page audit. Listed so it is visibly not skipped by accident. | No |

`ai_assisted` is new in this roadmap. It is deliberately distinct from
`automated` so the report and VPAT never hide that a model — not a
deterministic rule — produced the finding.

## Where we stand and where we're going

Starting distribution (A/AA, 55 SCs):

| Tier | Start | Target |
|---|---:|---:|
| `automated` | 21 | 23 |
| `ai_assisted` | 0 | 4 |
| `partial` | 21 | 20 |
| `manual_only` | 13 | 8 |

Covered (`automated` + `ai_assisted`) goes **21 → 27 of 55 ≈ 49%**, and
counting `partial` at half-credit lands the blended figure at **~62%**.
The realistic ceiling is ~67%; anything past that requires watching
video/audio content or server-side timing logic we cannot see.

## The three levers

### Lever 1 — VLM judgement (the `ai_assisted` tier)

`audit/vlm.py` already ships opt-in vision checks. They cover the
content-quality SCs that fail today only because "is this *meaningful*?"
needs judgement:

| SC | VLM rule | Was | Now |
|---|---|---|---|
| 1.1.1 Non-text Content (alt usefulness) | `vlm-alt-unhelpful` | partial | **ai_assisted** |
| 2.4.4 Link Purpose | `vlm-link-ambiguous` | partial | **ai_assisted** |
| 2.4.6 Headings and Labels | `vlm-heading-hierarchy-mismatch` | partial | **ai_assisted** |
| 3.3.3 Error Suggestion | `vlm-error-unclear` | partial | **ai_assisted** |

**Gating rule:** an SC only graduates to `ai_assisted` once its rule
hits **≥0.9 precision** on the benchmark corpus. Precision over recall —
a false "you fail 1.4.1" destroys trust faster than a miss. Every
ai_assisted finding carries a confidence and a human-confirm flag and
fails closed (no API key → check is skipped, never assumed-pass).

Candidate next moves for this lever (not yet promoted — need fixtures
and precision measurement): 1.4.5 Images of Text, 2.4.2 Page Titled
(descriptive?), 1.3.2 Meaningful Sequence (visual vs DOM order).

### Lever 2 — Mechanical re-tiering of mis-classified `manual_only`

Several `manual_only` SCs are not actually un-automatable — they were
just unbuilt. This roadmap ships two and scopes the rest:

| SC | Detection | Module | Status |
|---|---|---|---|
| 2.1.4 Character Key Shortcuts | `accesskey` attrs + single-character keydown handlers without modifier guard | `audit/char_key_shortcuts.py` | **shipped → partial** |
| 2.2.1 Timing Adjustable | `<meta http-equiv="refresh">` timeout / auto-redirect | `audit/timing.py` | **shipped → partial** |
| 2.4.5 Multiple Ways | ≥2 of {site search, nav landmark, sitemap link, breadcrumb} present | `audit/multiple_ways.py` | planned |
| 2.3.1 Three Flashes | frame-diff on a short screen recording (PEAT-style) | `audit/flash.py` | planned (needs video capture) |
| 3.2.4 Consistent Identification | cross-page icon/label consistency | extend `consistent_help` | planned (multi-page) |

Pointer SCs 2.5.1 / 2.5.2 / 2.5.4 already have detection in
`audit/mobile.py`; the registry notes track them and they are candidates
to lift from `manual_only` once their precision is measured on fixtures.

### Lever 3 — Journey/interaction detection (Chrome-driven)

The behavioural SCs currently at `partial` need *real interaction
state*, which a driveable browser supplies. These already have stub or
DSL-based modules; the lever is wiring richer interaction coverage, not
inventing detection:

- 3.2.1 On Focus · 3.2.2 On Input (`audit/dynamic.py` DSL)
- 4.1.3 Status Messages (`audit/live_regions.py`)
- 3.3.1 Error Identification (`audit/error_flow.py`)
- 3.3.8 Accessible Authentication (`audit/accessible_auth.py`)
- 2.5.7 Dragging (`audit/dragging.py`)
- 3.2.3 Consistent Navigation (cross-page)

These stay `partial` until interaction coverage is broad enough and
measured; this roadmap does not reclassify them yet.

## The hard floor (≈15–18%, never automatable from a page audit)

Be upfront in every report: these stay `manual_only` permanently.

- 1.2.1 / 1.2.3 / 1.2.4 / 1.2.5 — audio/video content *quality*
  (transcript accuracy, audio-description completeness). We cannot watch
  the video and judge it.
- 1.3.3 Sensory Characteristics — "click the green button on the right".
- 2.2.1 server-side session timeouts — partially visible at best.
- Real-user / lived-experience barriers — no automation substitutes for
  testing with disabled participants.

Anyone claiming 85%+ automation is moving the goalposts.

## Measurement is the deliverable, not an afterthought

A tier promotion is only legitimate when the benchmark proves it.

1. `benchmarks/corpus/<name>/page.html` — a minimal fixture, one pattern
   per fixture.
2. `benchmarks/corpus/<name>/ground_truth.yaml` — declared `expected` /
   `forbidden` rule firings.
3. `python benchmarks/run_benchmark.py` — per-rule precision/recall;
   exit 1 on any unexpected or missed finding.

Promotion checklist for any SC moving up a tier:

- [ ] ≥3 positive fixtures (rule should fire) and ≥1 negative (rule must
      not fire) in the corpus.
- [ ] Measured precision ≥0.9 (`ai_assisted` and `automated`) or ≥0.7
      (`partial`).
- [ ] Rule registered in `audit/rule_versions.py`.
- [ ] Registry `notes` explain exactly what is and isn't caught.
- [ ] `tests/test_wcag_coverage.py` locks the new tier against silent
      regression.
