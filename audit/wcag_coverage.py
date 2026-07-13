"""WCAG 2.2 coverage registry.

Single source of truth for "which Success Criteria do we meaningfully
test, and which are out of scope for automation?" Surfaced in the
report so a stakeholder cannot mistake an audit for proof of full
WCAG 2.2 conformance.

Four coverage tiers, ordered by trust:

  - **automated**       — we have a deterministic rule or set of rules
                          that flag common failures of this SC with high
                          confidence (attribute present/absent, DOM
                          relationship, geometry). Manual review remains
                          advisable for edge cases but not strictly
                          required for a first-pass conformance read.
  - **ai_assisted**     — a vision/LLM judgement makes the call where a
                          deterministic rule cannot (is the alt text
                          *useful?*, is the error message *clear?*).
                          Reported with confidence and ALWAYS flagged
                          for human confirmation — never a silent pass.
                          Fails closed: with no model configured the
                          check is skipped, not assumed-passed. See
                          docs/automation_roadmap.md for the promotion
                          gate (>=0.9 measured precision on the corpus).
  - **partial**         — automation catches some failure modes; the
                          SC fundamentally requires human judgement
                          (e.g. is alt text *meaningful?*).
  - **manual_only**     — out of scope for automation. Listed so it's
                          visible that we did not skip it by accident.

Adding a new criterion here is the contract for "we now cover X" — the
report renders directly off this registry. The roadmap that governs
tier promotions lives in docs/automation_roadmap.md.
"""

from __future__ import annotations

from typing import Any

# Canonical structure: criterion → coverage entry. Keep the keys sorted
# to make diffs readable.
#
# `notes` is the human-facing explanation surfaced in the report when
# the SC is not at `automated` tier — tells the auditor *why* manual
# review is needed.

CoverageEntry = dict[str, Any]

COVERAGE: dict[str, CoverageEntry] = {
    # ------------------------------------------------------------------
    # 1. Perceivable
    "1.1.1": {"level": "A",   "name": "Non-text Content",
              "tier": "ai_assisted",
              "notes": "Missing-alt and empty-alt detected deterministically; meaningful-alt judged by the opt-in VLM check (vlm-alt-unhelpful), flagged for human confirmation. Without a configured model this falls back to the deterministic subset only."},
    "1.2.1": {"level": "A",   "name": "Audio-only and Video-only (Prerecorded)",
              "tier": "manual_only",
              "notes": "Existence and quality of transcripts/audio descriptions cannot be machine-verified."},
    "1.2.2": {"level": "A",   "name": "Captions (Prerecorded)",
              "tier": "partial",
              "notes": "<track kind='captions'> presence is detected; content quality requires human review."},
    "1.2.3": {"level": "A",   "name": "Audio Description or Media Alternative (Prerecorded)",
              "tier": "manual_only", "notes": "Requires human review of audio-described or text-equivalent content."},
    "1.2.4": {"level": "AA",  "name": "Captions (Live)", "tier": "manual_only",
              "notes": "Live caption availability cannot be detected from a static audit."},
    "1.2.5": {"level": "AA",  "name": "Audio Description (Prerecorded)", "tier": "manual_only",
              "notes": "Requires human review of audio descriptions."},
    "1.3.1": {"level": "A",   "name": "Info and Relationships", "tier": "automated",
              "notes": "Headings, lists, landmarks, table semantics, label associations covered."},
    "1.3.2": {"level": "A",   "name": "Meaningful Sequence", "tier": "partial",
              "notes": "DOM order is checked; visual reading order against absolute-positioned content still needs human review."},
    "1.3.3": {"level": "A",   "name": "Sensory Characteristics", "tier": "manual_only",
              "notes": "References like 'click the green button on the right' require semantic understanding."},
    "1.3.4": {"level": "AA",  "name": "Orientation", "tier": "automated",
              "notes": "CSS that locks orientation is flagged."},
    "1.3.5": {"level": "AA",  "name": "Identify Input Purpose", "tier": "automated",
              "notes": "Autocomplete attribute coverage on common form fields is checked."},
    "1.4.1": {"level": "A",   "name": "Use of Color", "tier": "partial",
              "notes": "Heuristic detection of color-only inline markers and color-only links; broader uses of color still need human review."},
    "1.4.2": {"level": "A",   "name": "Audio Control", "tier": "automated",
              "notes": "Auto-playing media without controls is detected."},
    "1.4.3": {"level": "AA",  "name": "Contrast (Minimum)", "tier": "automated",
              "notes": "Computed and pixel-sampled contrast both supported."},
    "1.4.4": {"level": "AA",  "name": "Resize Text", "tier": "automated",
              "notes": "viewport user-scalable=no and text-overflow at 200% zoom checked."},
    "1.4.5": {"level": "AA",  "name": "Images of Text", "tier": "partial",
              "notes": "Image filenames hinting text content are flagged; content of images requires VLM or human review."},
    "1.4.10": {"level": "AA", "name": "Reflow", "tier": "automated",
               "notes": "Tested at 320 CSS px; content overflow and horizontal-scroll flagged."},
    "1.4.11": {"level": "AA", "name": "Non-text Contrast", "tier": "partial",
               "notes": "Focus indicators and adjacent-color contrast covered with pixel_analysis; icon contrast still partial."},
    "1.4.12": {"level": "AA", "name": "Text Spacing", "tier": "automated",
               "notes": "Override text-spacing CSS and verify no clipping/overlap."},
    "1.4.13": {"level": "AA", "name": "Content on Hover or Focus", "tier": "automated",
               "notes": "Tooltip/popover triggers actively probed for dismissibility (Escape) and hoverability (pointer-onto-content); persistence still relies on the existing dynamic interaction DSL."},

    # ------------------------------------------------------------------
    # 2. Operable
    "2.1.1": {"level": "A",   "name": "Keyboard", "tier": "automated",
              "notes": "Full keyboard tab walk + reachable-elements coverage."},
    "2.1.2": {"level": "A",   "name": "No Keyboard Trap", "tier": "automated",
              "notes": "Focus-cycle detection at the keyboard module."},
    "2.1.4": {"level": "A",   "name": "Character Key Shortcuts", "tier": "partial",
              "notes": "Single-character accesskey attributes and unguarded single-key keydown handlers are flagged (char_key_shortcuts module). Whether a turn-off / remap / focus-scope mechanism exists still needs human verification."},
    "2.2.1": {"level": "A",   "name": "Timing Adjustable", "tier": "partial",
              "notes": "Client-side <meta http-equiv='refresh'> time limits / auto-redirects are flagged (timing module). Server-driven session timeouts remain invisible to a page audit and need manual review."},
    "2.2.2": {"level": "A",   "name": "Pause, Stop, Hide", "tier": "partial",
              "notes": "Auto-animation > 5s and infinite carousels are flagged; user-pause control coverage requires interaction."},
    "2.3.1": {"level": "A",   "name": "Three Flashes or Below Threshold", "tier": "manual_only",
              "notes": "Flash analysis requires video recording; out of scope."},
    "2.4.1": {"level": "A",   "name": "Bypass Blocks", "tier": "automated",
              "notes": "Skip-link presence, target focusability, and live activation (Tab + Enter moves focus) all verified."},
    "2.4.2": {"level": "A",   "name": "Page Titled", "tier": "partial",
              "notes": "Empty, too-short, and generic-placeholder titles flagged; whether a non-generic title is *meaningful* is human-judged."},
    "2.4.3": {"level": "A",   "name": "Focus Order", "tier": "automated",
              "notes": "Tab order vs DOM/visual order checked; dynamic flows via interactions DSL."},
    "2.4.4": {"level": "A",   "name": "Link Purpose (In Context)", "tier": "ai_assisted",
              "notes": "Empty / placeholder text flagged deterministically; context-specific clarity judged by the opt-in VLM check (vlm-link-ambiguous), flagged for human confirmation."},
    "2.4.5": {"level": "AA",  "name": "Multiple Ways", "tier": "manual_only",
              "notes": "Navigation patterns across a page set; not detectable from a single audit."},
    "2.4.6": {"level": "AA",  "name": "Headings and Labels", "tier": "ai_assisted",
              "notes": "Empty/duplicate headings flagged deterministically; descriptive-ness and visual-vs-DOM hierarchy judged by the opt-in VLM check (vlm-heading-hierarchy-mismatch), flagged for human confirmation."},
    "2.4.7": {"level": "AA",  "name": "Focus Visible", "tier": "automated",
              "notes": "Default focus-removal patterns + pixel-sampled indicator visibility."},
    "2.4.11": {"level": "AA", "name": "Focus Not Obscured (Minimum)", "tier": "automated",
               "notes": "Every keyboard tab stop is intersected with sticky/fixed-positioned overlays; full-coverage geometry check."},
    "2.5.1": {"level": "A",   "name": "Pointer Gestures", "tier": "manual_only",
              "notes": "Multi-touch / path-based gestures cannot be inventoried statically."},
    "2.5.2": {"level": "A",   "name": "Pointer Cancellation", "tier": "manual_only",
              "notes": "down-event handlers without up-cancellation require runtime observation."},
    "2.5.3": {"level": "A",   "name": "Label in Name", "tier": "automated",
              "notes": "Visible-text vs accessible-name analysis at every tab stop."},
    "2.5.4": {"level": "A",   "name": "Motion Actuation", "tier": "manual_only",
              "notes": "Use of devicemotion/deviceorientation events without alternatives."},
    "2.5.7": {"level": "AA",  "name": "Dragging Movements", "tier": "partial",
              "notes": "Heuristic detection of draggable elements without sibling alternatives + keyboard-less ARIA sliders; some essential-drag flows still need human review. New in WCAG 2.2."},
    "2.5.8": {"level": "AA",  "name": "Target Size (Minimum)", "tier": "automated",
              "notes": "24x24 CSS px minimum; inline + spacing exceptions detected. New in WCAG 2.2."},

    # ------------------------------------------------------------------
    # 3. Understandable
    "3.1.1": {"level": "A",   "name": "Language of Page", "tier": "automated",
              "notes": "<html lang=...> presence + ISO validity + content-script vs declared-lang mismatch detection."},
    "3.1.2": {"level": "AA",  "name": "Language of Parts", "tier": "partial",
              "notes": "lang= changes inside the document detected; correctness requires text review."},
    "3.2.1": {"level": "A",   "name": "On Focus", "tier": "partial",
              "notes": "Focus-induced navigation/submission detected via interactions DSL."},
    "3.2.2": {"level": "A",   "name": "On Input", "tier": "partial",
              "notes": "Same as 3.2.1 for input events; full coverage needs interaction tests."},
    "3.2.3": {"level": "AA",  "name": "Consistent Navigation", "tier": "partial",
              "notes": "Cross-page nav-order comparison covered alongside 3.2.6."},
    "3.2.4": {"level": "AA",  "name": "Consistent Identification", "tier": "manual_only",
              "notes": "Identification consistency requires semantic understanding."},
    "3.2.6": {"level": "A",   "name": "Consistent Help", "tier": "automated",
              "notes": "Cross-page help-mechanism order is compared. New in WCAG 2.2."},
    "3.3.1": {"level": "A",   "name": "Error Identification", "tier": "partial",
              "notes": "Error-text linkage to fields detected; error wording quality needs review."},
    "3.3.2": {"level": "A",   "name": "Labels or Instructions", "tier": "automated",
              "notes": "Label coverage on form controls is checked."},
    "3.3.3": {"level": "AA",  "name": "Error Suggestion", "tier": "ai_assisted",
              "notes": "Vague error text flagged deterministically; whether an error tells the user HOW to fix it judged by the opt-in VLM check (vlm-error-unclear), flagged for human confirmation."},
    "3.3.4": {"level": "AA",  "name": "Error Prevention (Legal, Financial, Data)", "tier": "partial",
              "notes": "Caller-declared form_consequence determines whether the rule applies."},
    "3.3.7": {"level": "A",   "name": "Redundant Entry", "tier": "partial",
              "notes": "Single-page heuristic: same field name across forms without autocomplete tokens. Multi-step state tracking still requires the dynamic interaction DSL. New in WCAG 2.2."},
    "3.3.8": {"level": "AA",  "name": "Accessible Authentication (Minimum)", "tier": "partial",
              "notes": "CAPTCHA detection (Recaptcha, hCaptcha, Turnstile, Arkose, Geetest) + cognitive-test prompt heuristics on auth pages. The presence of an alternative path still needs human verification. New in WCAG 2.2."},

    # ------------------------------------------------------------------
    # 4. Robust
    "4.1.2": {"level": "A",   "name": "Name, Role, Value", "tier": "automated",
              "notes": "ARIA role validity, required ARIA properties, and round-trip state changes covered."},
    "4.1.3": {"level": "AA",  "name": "Status Messages", "tier": "partial",
              "notes": "Live-region inventory + role/aria-live conflict detection + empty-on-load checks; *announcement* still requires declared interactions to verify the trigger fires."},
}


def report(target_level: str = "AA") -> dict[str, Any]:
    """Return a structured coverage report.

    `target_level` filters which criteria to include — "A" returns
    only level-A SCs, "AA" returns A+AA (the realistic compliance
    target for most public-facing sites), "AAA" returns everything.
    Anything below the requested level is included; anything above
    is excluded.
    """
    rank = {"A": 0, "AA": 1, "AAA": 2}
    cap = rank.get(target_level.upper(), 1)

    automated: list[dict[str, Any]] = []
    ai_assisted: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    manual_only: list[dict[str, Any]] = []
    by_tier = {
        "automated": automated,
        "ai_assisted": ai_assisted,
        "partial": partial,
        "manual_only": manual_only,
    }

    for sc, entry in COVERAGE.items():
        if rank.get(entry["level"], 9) > cap:
            continue
        record = {"sc": sc, **entry}
        bucket = by_tier.get(entry["tier"])
        if bucket is not None:
            bucket.append(record)

    # Stable order by SC number so diffs are readable.
    def _sort_key(e: dict[str, Any]) -> tuple:
        parts = [int(p) for p in e["sc"].split(".") if p.isdigit()]
        return tuple(parts)

    for bucket in by_tier.values():
        bucket.sort(key=_sort_key)

    total_in_scope = sum(len(b) for b in by_tier.values())
    # "covered" = automated + ai_assisted (both make a call without
    # per-page human review). Surfaced so the report and any published
    # coverage figure read off one computed number rather than re-summing
    # tiers by hand and drifting. NOT a conformance claim — see
    # docs/automation_roadmap.md on the SC-coverage vs barrier-catch
    # distinction.
    covered = len(automated) + len(ai_assisted)
    return {
        "target_level": target_level.upper(),
        "totals": {
            "in_scope": total_in_scope,
            "automated": len(automated),
            "ai_assisted": len(ai_assisted),
            "partial": len(partial),
            "manual_only": len(manual_only),
            "covered": covered,
            # Blended coverage counting partial at half credit, rounded
            # to whole percent. Honest middle figure between "only count
            # fully-covered" and "count anything we touch".
            "covered_pct": (
                round(100 * (covered + 0.5 * len(partial)) / total_in_scope)
                if total_in_scope else 0
            ),
        },
        "automated": automated,
        "ai_assisted": ai_assisted,
        "partial": partial,
        "manual_only": manual_only,
    }
