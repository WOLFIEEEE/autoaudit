"""Generate a VPAT 2.5-shaped conformance report.

A VPAT (Voluntary Product Accessibility Template, ITI v2.5) is the
standard procurement-ready format that answers, per WCAG Success
Criterion: "Supports / Partially Supports / Does Not Support / Not
Applicable", with remarks. We generate the WCAG 2.x tab of the VPAT.

This module produces two outputs:
  - `build_vpat(audit_result)` -> a dict shaped like the VPAT tab
    (machine-consumable for templating into Word/HTML/Excel).
  - `render_vpat_html(audit_result)` -> a self-contained HTML rendering
    suitable for handing to a buyer.

We do NOT try to output a VPAT .docx directly — the format is
proprietary to ITI and the fidelity gap between the template and
what our HTML produces isn't worth the complexity of writing Office
XML by hand. Buyers typically paste our HTML into their official VPAT.
"""

from __future__ import annotations

from html import escape
from typing import Any

from audit._wcag import _WCAG_LEVELS  # type: ignore[attr-defined]
from audit.wcag_coverage import COVERAGE as _COVERAGE

# VPAT conformance categories, per ITI 2.5 spec (section "Terms").
SUPPORTS = "Supports"
PARTIAL = "Partially Supports"
DOES_NOT_SUPPORT = "Does Not Support"
NOT_APPLICABLE = "Not Applicable"
NOT_EVALUATED = "Not Evaluated"

# Full WCAG 2.2 SC labels (mirrors _WCAG_LEVELS in audit/_wcag.py, but
# with the SC title appended — the VPAT table needs the friendly name).
_SC_TITLES: dict[str, str] = {
    "1.1.1": "Non-text Content",
    "1.2.1": "Audio-only and Video-only (Prerecorded)",
    "1.2.2": "Captions (Prerecorded)",
    "1.2.3": "Audio Description or Media Alternative (Prerecorded)",
    "1.2.4": "Captions (Live)",
    "1.2.5": "Audio Description (Prerecorded)",
    "1.2.6": "Sign Language (Prerecorded)",
    "1.2.7": "Extended Audio Description (Prerecorded)",
    "1.2.8": "Media Alternative (Prerecorded)",
    "1.2.9": "Audio-only (Live)",
    "1.3.1": "Info and Relationships",
    "1.3.2": "Meaningful Sequence",
    "1.3.3": "Sensory Characteristics",
    "1.3.4": "Orientation",
    "1.3.5": "Identify Input Purpose",
    "1.3.6": "Identify Purpose",
    "1.4.1": "Use of Color",
    "1.4.2": "Audio Control",
    "1.4.3": "Contrast (Minimum)",
    "1.4.4": "Resize Text",
    "1.4.5": "Images of Text",
    "1.4.6": "Contrast (Enhanced)",
    "1.4.7": "Low or No Background Audio",
    "1.4.8": "Visual Presentation",
    "1.4.9": "Images of Text (No Exception)",
    "1.4.10": "Reflow",
    "1.4.11": "Non-text Contrast",
    "1.4.12": "Text Spacing",
    "1.4.13": "Content on Hover or Focus",
    "2.1.1": "Keyboard",
    "2.1.2": "No Keyboard Trap",
    "2.1.3": "Keyboard (No Exception)",
    "2.1.4": "Character Key Shortcuts",
    "2.2.1": "Timing Adjustable",
    "2.2.2": "Pause, Stop, Hide",
    "2.2.3": "No Timing",
    "2.2.4": "Interruptions",
    "2.2.5": "Re-authenticating",
    "2.2.6": "Timeouts",
    "2.3.1": "Three Flashes or Below Threshold",
    "2.3.2": "Three Flashes",
    "2.3.3": "Animation from Interactions",
    "2.4.1": "Bypass Blocks",
    "2.4.2": "Page Titled",
    "2.4.3": "Focus Order",
    "2.4.4": "Link Purpose (In Context)",
    "2.4.5": "Multiple Ways",
    "2.4.6": "Headings and Labels",
    "2.4.7": "Focus Visible",
    "2.4.8": "Location",
    "2.4.9": "Link Purpose (Link Only)",
    "2.4.10": "Section Headings",
    "2.4.11": "Focus Not Obscured (Minimum)",
    "2.4.12": "Focus Not Obscured (Enhanced)",
    "2.4.13": "Focus Appearance",
    "2.5.1": "Pointer Gestures",
    "2.5.2": "Pointer Cancellation",
    "2.5.3": "Label in Name",
    "2.5.4": "Motion Actuation",
    "2.5.5": "Target Size (Enhanced)",
    "2.5.6": "Concurrent Input Mechanisms",
    "2.5.7": "Dragging Movements",
    "2.5.8": "Target Size (Minimum)",
    "3.1.1": "Language of Page",
    "3.1.2": "Language of Parts",
    "3.1.3": "Unusual Words",
    "3.1.4": "Abbreviations",
    "3.1.5": "Reading Level",
    "3.1.6": "Pronunciation",
    "3.2.1": "On Focus",
    "3.2.2": "On Input",
    "3.2.3": "Consistent Navigation",
    "3.2.4": "Consistent Identification",
    "3.2.5": "Change on Request",
    "3.2.6": "Consistent Help",
    "3.3.1": "Error Identification",
    "3.3.2": "Labels or Instructions",
    "3.3.3": "Error Suggestion",
    "3.3.4": "Error Prevention (Legal, Financial, Data)",
    "3.3.5": "Help",
    "3.3.6": "Error Prevention (All)",
    "3.3.7": "Redundant Entry",
    "3.3.8": "Accessible Authentication (Minimum)",
    "3.3.9": "Accessible Authentication (Enhanced)",
    "4.1.1": "Parsing (Obsolete and Removed)",
    "4.1.2": "Name, Role, Value",
    "4.1.3": "Status Messages",
}


def build_vpat(audit: dict[str, Any], *, target_level: str = "AA") -> dict[str, Any]:
    """Produce a dict keyed by SC id with per-SC conformance judgment.

    Conformance heuristic (honest and conservative):
      - Any open issue with this SC in its wcag_criteria → Does Not Support
      - SC was in scope for this audit but we ran no rule that maps to
        it (e.g. 1.2.1 audio) → Not Evaluated
      - SC is AAA and target_level=AA → Not Applicable (out of scope
        for conformance claim)
      - Otherwise (SC in scope, no issues) → Supports
    """
    # Gather which SCs any of our issues touched.
    issues = audit.get("issues") or []
    sc_with_issues: dict[str, list[dict[str, Any]]] = {}
    for iss in issues:
        for sc in iss.get("wcag_criteria") or []:
            sc_with_issues.setdefault(sc, []).append(iss)

    # Which SCs are "covered" by our rule set — i.e. some rule could
    # in principle fire. Without this tracking the "Not Evaluated"
    # distinction is meaningless. We derive it from the union of SCs
    # that appear on any rule firing, plus a static allowlist of SCs
    # our rule set targets even when they don't fire on this page.
    evaluated_scs = set(sc_with_issues) | _COVERED_SCS

    level_rank = {"A": 0, "AA": 1, "AAA": 2}
    target_rank = level_rank[target_level]

    rows: list[dict[str, Any]] = []
    for sc, level in sorted(_WCAG_LEVELS.items(), key=_sc_sort_key):
        # 4.1.1 Parsing was removed in WCAG 2.2 — _WCAG_LEVELS keeps it
        # tagged OBSOLETE so other lookups still recognize the SC, but it
        # has no conformance row in a 2.2 VPAT. Skip it (otherwise
        # level_rank[level] KeyErrors on "OBSOLETE").
        if level not in level_rank:
            continue
        in_scope = level_rank[level] <= target_rank
        remarks = ""
        # Look up the coverage tier the audit actually offers for this
        # SC. The tier governs how we down-grade an apparent "Supports"
        # — e.g. an SC where automation only covers part of the
        # failure space cannot legitimately claim Supports without
        # human verification, even when zero rules fire.
        coverage = _COVERAGE.get(sc)
        cov_tier = (coverage or {}).get("tier")
        # Confidence tiers from the issues themselves: a serious
        # finding stamped `confidence: high` should weigh differently
        # in the VPAT remarks than a `confidence: low` heuristic
        # nudge. We surface both counts so reviewers know what they
        # are looking at.
        sc_issues = sc_with_issues.get(sc) or []
        conf_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
        for iss in sc_issues:
            c = (iss.get("confidence") or "unknown").lower()
            conf_counts[c if c in conf_counts else "unknown"] += 1
        if not in_scope:
            conformance = NOT_APPLICABLE
            remarks = f"Level {level} criterion — outside target level {target_level}."
        elif sc_issues:
            # Confidence-tiered conformance:
            #   - any high-confidence finding → Does Not Support
            #   - only low-confidence findings → Partially Supports
            #     (worth a reviewer's attention, but not strong enough
            #     to assert non-conformance unilaterally)
            #   - mixed: at least one medium → Does Not Support
            high = conf_counts["high"]
            med = conf_counts["medium"]
            low = conf_counts["low"]
            n = len(sc_issues)
            if high or med:
                conformance = DOES_NOT_SUPPORT
                remarks = (
                    f"{n} issue{'s' if n != 1 else ''} found "
                    f"(high={high}, medium={med}, low={low}); "
                    "see detailed report."
                )
            else:
                conformance = PARTIAL
                remarks = (
                    f"{low} low-confidence heuristic finding"
                    f"{'s' if low != 1 else ''} — review and "
                    "confirm or dismiss before publishing."
                )
        elif sc in evaluated_scs and cov_tier == "automated":
            conformance = SUPPORTS
            remarks = "No issues detected; this SC is fully covered by automated rules."
        elif cov_tier == "ai_assisted":
            # An AI/VLM judgement is the basis of coverage here. Even
            # with zero findings we will not claim Supports off a model's
            # word — that is the whole point of keeping ai_assisted
            # distinct from automated. Partially Supports with an
            # explicit human-confirmation remark.
            conformance = PARTIAL
            remarks = (
                "Covered by an AI-assisted (vision/LLM) check rather than "
                "a deterministic rule. No failure was reported, but the "
                "model's judgement must be confirmed by a human reviewer "
                "before claiming Supports. If no model was configured, "
                "only the deterministic subset of this criterion was "
                "evaluated."
            )
        elif sc in evaluated_scs and cov_tier == "partial":
            # Some failure modes covered by automation, others not.
            # Refusing to claim Supports without manual review is the
            # whole point of the confidence tiering.
            conformance = PARTIAL
            remarks = (
                "Automated rules detected no failure, but this SC "
                "is only partially covered by automation. Manual "
                "review of the un-covered failure modes is required "
                "before claiming Supports."
            )
        elif sc in evaluated_scs:
            # Issue list mentions this SC (so axe / a custom rule
            # touched it) but coverage registry says it's manual-only
            # or unspecified. Fall through to Not Evaluated with a
            # clear remark — better than a misleading Supports claim.
            conformance = NOT_EVALUATED
            remarks = (
                "Our automated rule set does not provide complete "
                "coverage for this criterion. Manual review required."
            )
        else:
            conformance = NOT_EVALUATED
            remarks = (
                "Our automated rule set does not evaluate this criterion. "
                "Manual review required."
            )
        rows.append({
            "sc": sc,
            "title": _SC_TITLES.get(sc, ""),
            "level": level,
            "conformance": conformance,
            "remarks": remarks,
            # Free-text guidance for a human reviewer. Only populated
            # for SCs that demand manual verification (presence in
            # EVALUATION_GUIDANCE). Helps teams filling an ACR know
            # what to look at for Not Evaluated / Partial rows.
            "evaluation_guidance": EVALUATION_GUIDANCE.get(sc, ""),
            "issue_count": len(sc_issues),
            # Per-tier confidence breakdown so VPAT consumers can
            # see at a glance whether a Does-Not-Support is backed
            # by a hard finding or a heuristic.
            "confidence_breakdown": conf_counts,
            "coverage_tier": cov_tier or "unspecified",
        })

    summary = audit.get("summary") or {}
    return {
        "vpat_version": "2.5",
        "target_level": target_level,
        "audit_url": audit.get("url"),
        "audit_timestamp": audit.get("timestamp"),
        "overall_claim": _overall_claim(rows, target_level),
        "rows": rows,
        "summary_counts": {
            "supports": sum(1 for r in rows if r["conformance"] == SUPPORTS),
            "partially_supports": sum(1 for r in rows if r["conformance"] == PARTIAL),
            "does_not_support": sum(1 for r in rows if r["conformance"] == DOES_NOT_SUPPORT),
            "not_applicable": sum(1 for r in rows if r["conformance"] == NOT_APPLICABLE),
            "not_evaluated": sum(1 for r in rows if r["conformance"] == NOT_EVALUATED),
        },
        "overall_score": summary.get("score"),
        "overall_grade": summary.get("grade"),
    }


def _overall_claim(rows: list[dict[str, Any]], target_level: str) -> str:
    """Honest overall-claim line. We refuse to claim conformance at or
    above the target level when any in-scope SC is in Does-Not-Support."""
    fails = [r for r in rows
             if r["conformance"] == DOES_NOT_SUPPORT]
    if fails:
        return f"Does NOT meet WCAG 2.2 Level {target_level} (open {len(fails)} SC failures)."
    evaluated = [r for r in rows
                 if r["conformance"] in (SUPPORTS, DOES_NOT_SUPPORT, PARTIAL)
                 and r["level"] in _target_levels(target_level)]
    total_in_scope = [r for r in rows if r["level"] in _target_levels(target_level)]
    return (
        f"Conforms to WCAG 2.2 Level {target_level} for evaluated criteria "
        f"({len(evaluated)}/{len(total_in_scope)} in-scope SCs evaluated)."
    )


def _target_levels(level: str) -> set[str]:
    return {"A": {"A"}, "AA": {"A", "AA"}, "AAA": {"A", "AA", "AAA"}}[level]


def _sc_sort_key(item: tuple[str, str]) -> tuple[int, ...]:
    """Natural sort for SC ids like '1.4.10' — string sort puts 10
    before 2, which is wrong for VPAT readability."""
    sc = item[0]
    parts = sc.split(".")
    return tuple(int(p) for p in parts)


# SCs our rule set actively targets (via custom rules or axe coverage).
# Used to distinguish "Supports" from "Not Evaluated". Maintained here
# because it's a product-level claim — which SCs do we test — that
# shouldn't drift with implementation details. Add an SC here only
# when we're confident a failure of it would fire at least one rule.
_COVERED_SCS: set[str] = {
    # Perceivable
    "1.1.1",                            # non-text content (alt)
    "1.3.1", "1.3.2", "1.3.5",          # info-relationships, meaningful-sequence, input purpose (autocomplete)
    "1.4.1", "1.4.2", "1.4.3", "1.4.4",
    "1.4.10", "1.4.11", "1.4.13",
    # Operable
    "2.1.1", "2.1.2", "2.1.4",
    "2.2.2",
    "2.4.1", "2.4.2", "2.4.3", "2.4.4", "2.4.6", "2.4.7",
    "2.4.11", "2.4.13",
    "2.5.1", "2.5.3", "2.5.4", "2.5.7", "2.5.8",
    "1.3.4",
    # Understandable
    "3.1.1", "3.1.2",
    "3.2.1", "3.2.2",
    "3.3.1", "3.3.2", "3.3.3", "3.3.4",
    # Robust
    "4.1.2", "4.1.3",
}


# Per-SC evaluation guidance for human reviewers, used in the VPAT
# rendering when conformance is "Not Evaluated" (or when a reviewer
# wants a reminder of what to check). Keys match _WCAG_LEVELS. Only
# SCs that require manual review get an entry — fully automated ones
# are deliberately absent so the VPAT doesn't pretend SMEs need to
# re-check them.
EVALUATION_GUIDANCE: dict[str, str] = {
    "1.1.1": (
        "Automated rules detect missing/placeholder alt. Manual review "
        "required to judge whether a non-empty alt actually describes "
        "the image's function/meaning."
    ),
    "1.2.1": (
        "Automated tools cannot check transcript quality. Manually "
        "verify that audio-only / video-only prerecorded content has "
        "an equivalent transcript or audio description."
    ),
    "1.2.2": (
        "We detect presence of <track> but not caption accuracy. "
        "Manually verify prerecorded video captions are complete, "
        "correct, and synchronized with speech."
    ),
    "1.2.3": (
        "Manually verify audio description exists and narrates the "
        "visual information a blind viewer would otherwise miss."
    ),
    "1.2.4": (
        "Live captioning is runtime-only. Observe a live session and "
        "verify captions appear within a few seconds of speech."
    ),
    "1.2.5": (
        "Separate audio-description track — manual inspection required."
    ),
    "1.4.1": (
        "axe-core catches color-only indicators in some common cases "
        "(e.g. 'required: *' patterns). Manually verify that status, "
        "required-ness, and link differentiation also use text, icon, "
        "or underline — not color alone."
    ),
    "1.4.3": (
        "Our pixel-contrast module (enable with pixel_analysis=true) "
        "samples visible text and measures against AA thresholds. "
        "Manually verify text over complex images/gradients where "
        "automated measurement may miss the actual background."
    ),
    "1.4.5": (
        "Automated tools cannot distinguish images-of-text from "
        "decorative images. Review manually for logos / banners / "
        "graphics where text could have been live HTML."
    ),
    "1.4.10": (
        "We test reflow at 320 CSS pixels. Manually verify that "
        "content remains usable when zoomed to 400% in the browser, "
        "and that no horizontal scrolling is required."
    ),
    "1.4.11": (
        "Our pixel-focus module measures focus-indicator contrast. "
        "Manually review hover states, icon contrast against "
        "surrounding chrome, and custom UI-component boundaries."
    ),
    "1.4.12": (
        "Manually verify that applying text-spacing (line height 1.5, "
        "letter spacing 0.12em, word spacing 0.16em, paragraph spacing "
        "2x font size) doesn't break layout or clip content."
    ),
    "1.4.13": (
        "Manually trigger hover/focus content and verify it can be "
        "dismissed, remains visible while hovered, and persists until "
        "the trigger is removed."
    ),
    "2.1.1": (
        "Our keyboard walk covers focusable elements. Manually verify "
        "custom widgets (comboboxes, date pickers, menus) respond to "
        "Enter/Space/Arrow keys per APG."
    ),
    "2.2.1": (
        "Manually verify that any session timeout offers the user a "
        "way to extend it, and that any moving/blinking content can "
        "be paused."
    ),
    "2.3.1": (
        "Manually verify no content flashes more than 3 times per "
        "second in any 1-second window. Automated detection of flash "
        "is unreliable."
    ),
    "2.4.7": (
        "We detect missing/invisible outline styles. Manually verify "
        "the indicator is distinguishable in the page's color scheme "
        "(especially with high-contrast mode on)."
    ),
    "2.5.1": (
        "We flag inline touch handlers as manual-review candidates. "
        "Verify that every multi-touch or path-based gesture has a "
        "single-pointer alternative."
    ),
    "2.5.4": (
        "Our mobile module flags scripts referencing devicemotion. "
        "Manually verify motion actuation is (a) reversible via UI "
        "and (b) can be disabled."
    ),
    "3.1.3": (
        "Reading-level and unusual-word detection is subjective. "
        "Manually review with a plain-language checklist."
    ),
    "3.1.4": (
        "Manually verify that abbreviations have an expansion on "
        "first use or via <abbr> / aria-expanded."
    ),
    "3.3.4": (
        "When form_consequence is set we emit a suggestive rule. "
        "Manually verify that legal/financial/data submissions offer "
        "review, reversal, or explicit confirmation."
    ),
    "4.1.3": (
        "Our dynamic DSL tests specific live-region flows when "
        "declared. Manually verify that ALL status messages (loading "
        "spinners, toasts, inline validation) are announced to SR "
        "users via role=status or aria-live=polite."
    ),
}


def render_vpat_html(audit: dict[str, Any], *, target_level: str = "AA") -> str:
    """Self-contained HTML VPAT rendering — no external CSS/JS."""
    v = build_vpat(audit, target_level=target_level)
    rows_html = []
    for r in v["rows"]:
        conf_class = {
            SUPPORTS: "conf-ok",
            DOES_NOT_SUPPORT: "conf-fail",
            PARTIAL: "conf-partial",
            NOT_APPLICABLE: "conf-na",
            NOT_EVALUATED: "conf-eval",
        }[r["conformance"]]
        # Surface the evaluation guidance alongside the remarks so
        # reviewers can see what to verify for Not-Evaluated rows.
        remarks = escape(r["remarks"])
        guidance = r.get("evaluation_guidance") or ""
        if guidance:
            remarks += (
                f"<br><em class='conf-eval'>Manual review: "
                f"{escape(guidance)}</em>"
            )
        rows_html.append(
            f"<tr>"
            f"<td>{escape(r['sc'])}</td>"
            f"<td>{escape(r['title'])}</td>"
            f"<td>{escape(r['level'])}</td>"
            f"<td class='{conf_class}'>{escape(r['conformance'])}</td>"
            f"<td>{remarks}</td>"
            f"</tr>"
        )
    counts = v["summary_counts"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>VPAT 2.5 — {escape(v.get('audit_url') or '')}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.5rem 0.75rem; text-align: left; font-size: 0.9rem; vertical-align: top; }}
th {{ background: #f0f0f0; }}
.conf-ok {{ color: #1a7f37; font-weight: 600; }}
.conf-fail {{ color: #b00020; font-weight: 600; }}
.conf-partial {{ color: #d07000; font-weight: 600; }}
.conf-na {{ color: #666; }}
.conf-eval {{ color: #666; font-style: italic; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.5rem; }}
.stat {{ padding: 0.5rem; border: 1px solid #ddd; border-radius: 4px; }}
.stat .v {{ font-size: 1.5rem; font-weight: 600; }}
.stat .l {{ font-size: 0.75rem; color: #666; text-transform: uppercase; }}
</style>
</head><body>
<h1>VPAT 2.5 — WCAG 2.2 Conformance Report</h1>
<p><strong>URL audited:</strong> {escape(v.get('audit_url') or '—')}</p>
<p><strong>Audit timestamp:</strong> {escape(v.get('audit_timestamp') or '—')}</p>
<p><strong>Target conformance level:</strong> {escape(target_level)}</p>
<p><strong>Overall claim:</strong> {escape(v['overall_claim'])}</p>

<h2>Summary</h2>
<div class="summary">
  <div class="stat"><div class="v conf-ok">{counts['supports']}</div><div class="l">Supports</div></div>
  <div class="stat"><div class="v conf-partial">{counts['partially_supports']}</div><div class="l">Partially</div></div>
  <div class="stat"><div class="v conf-fail">{counts['does_not_support']}</div><div class="l">Does Not Support</div></div>
  <div class="stat"><div class="v conf-na">{counts['not_applicable']}</div><div class="l">N/A</div></div>
  <div class="stat"><div class="v conf-eval">{counts['not_evaluated']}</div><div class="l">Not Evaluated</div></div>
</div>

<h2>Detailed conformance — WCAG 2.2</h2>
<table>
  <thead>
    <tr><th>SC</th><th>Title</th><th>Level</th><th>Conformance</th><th>Remarks & Explanations</th></tr>
  </thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>

<p style="margin-top: 2rem; padding: 0.75rem; border: 1px dashed #999; border-radius: 6px; font-size: 0.9rem;">
<strong>Methodology.</strong> Automated audit using Chromium a11y-tree
analysis, axe-core 4.x, NVDA (focus + browse mode) on Windows, plus
custom heuristics. "Not Evaluated" means this Success Criterion is
outside our automated rule scope and requires manual review. Real
screen-reader coverage includes NVDA only; JAWS / VoiceOver / TalkBack
require manual testing.
</p>

</body></html>
"""
