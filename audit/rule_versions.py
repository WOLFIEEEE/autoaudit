"""Rule version registry — every rule has a semver-like version stamp.

Why this exists:

  A VPAT signed off in March 2026 should be auditable two years later
  without ambiguity about which detection logic was in effect. Today
  every issue carries a `rule` ID, but if the rule's threshold or
  criteria change, the prior audit's "Supports / Does Not Support"
  judgement could become inaccurate retroactively. Stamping a
  `rule_version` on every issue closes that gap.

How to use:

  1. Add an entry here when a new rule ships. Start at "1.0.0".
  2. Bump version on:
       - PATCH: rule wording / fix text / description; same logic.
       - MINOR: tightening that adds findings (more selectors,
                additional checks). Old reports remain valid; new
                reports may surface more.
       - MAJOR: loosening that removes findings or changes severity.
                A prior "Does Not Support" could become "Supports" —
                buyers re-running the audit may see different conformance.
  3. The orchestrator stamps every issue with `rule_version`
     automatically via `audit._issue.make_issue`. Issues without a
     registered version get "0.0.0" (signals "uncatalogued").
  4. Each audit result also exposes a `rule_set_hash` — SHA256 of the
     sorted (rule_id, version) pairs — so a CI run can pin and verify.

Test: `tests/test_rule_versions.py` enforces that every rule emitted
by any audit module is registered here.
"""

from __future__ import annotations

import hashlib

# When you bump a rule, update this dict and ALSO bump the meta-version
# below so consumers can detect "the rule set changed" without
# diffing every entry.
RULE_VERSIONS: dict[str, str] = {
    # -- structure / lang_detection --
    "structure-html-lang": "1.0.0",
    "structure-lang-content-mismatch": "1.0.0",
    "structure-title-missing": "1.0.0",
    "structure-title-too-short": "1.0.0",
    "structure-title-generic": "1.0.0",
    "structure-no-h1": "1.0.0",
    "structure-multiple-h1": "1.0.0",
    "structure-heading-skip": "1.0.0",
    "structure-duplicate-heading": "1.0.0",
    "structure-no-main": "1.0.0",
    "structure-table-no-th": "1.0.0",
    "structure-iframe-no-title": "1.0.0",
    "structure-iframe-title-generic": "1.0.0",
    "structure-lang-of-parts": "1.0.0",

    # -- aria --
    "aria-invalid-role": "1.0.0",
    "aria-labelledby-missing": "1.0.0",
    "aria-describedby-missing": "1.0.0",
    "aria-hidden-focusable": "1.0.0",

    # -- forms --
    "forms-input-no-label": "1.0.0",
    "forms-radio-group-no-fieldset": "1.0.0",
    "forms-aria-invalid-no-description": "1.0.0",
    "forms-error-not-descriptive": "1.0.0",
    "forms-missing-autocomplete": "1.0.0",
    "forms-autocomplete-on-off": "1.0.0",
    "forms-autocomplete-unknown-token": "1.0.0",
    "forms-no-review-step": "1.0.0",

    # -- keyboard --
    "keyboard-trap-suspected": "1.0.0",
    "keyboard-no-accessible-name": "1.0.0",
    "keyboard-no-focus-indicator": "1.0.0",
    "keyboard-positive-tabindex": "1.0.0",
    "keyboard-generic-focusable": "1.0.0",

    # -- target_size --
    "target-size-undersized": "1.0.0",
    "target-size-spacing-tight": "1.0.0",

    # -- consistent_help --
    "consistent-help-relative-order-changed": "1.0.0",

    # -- skiplinks --
    "skiplink-missing": "1.0.0",
    "skiplink-target-missing": "1.0.0",
    "skiplink-target-not-focusable": "1.0.0",
    "skiplink-broken": "1.0.0",

    # -- live_regions --
    "live-region-role-conflict": "1.0.0",
    "live-region-silenced": "1.0.0",
    "live-region-empty-on-load": "1.0.0",

    # -- color_only --
    "color-only-inline-marker": "1.0.0",
    "color-only-link": "1.0.0",

    # -- dragging --
    "dragging-handler-on-element": "1.0.0",
    "dragging-no-keyboard-alt": "1.0.0",

    # -- redundant_entry --
    "redundant-entry-no-autocomplete": "1.0.0",

    # -- accessible_auth --
    "accessible-auth-captcha-detected": "1.0.0",
    "accessible-auth-cognitive-test": "1.0.0",

    # -- char_key_shortcuts --
    "char-key-shortcut-accesskey": "1.0.0",
    "char-key-shortcut-single-key-handler": "1.0.0",

    # -- timing --
    "timing-meta-refresh": "1.0.0",
    "timing-meta-refresh-redirect": "1.0.0",

    # -- fake_button --
    "fake-button-noninteractive": "1.0.0",
    "fake-button-anchor-no-href": "1.0.0",

    # -- reveal --
    "disclosure-missing-expanded-state": "1.0.0",
    "reveal-undersized-target": "1.0.0",
    "reveal-control-unnamed": "1.0.0",
    "menu-focus-not-trapped": "1.0.0",
    "carousel-auto-advance": "1.0.0",
    "carousel-change-not-announced": "1.0.0",
    "carousel-region-no-name": "1.0.0",
    "carousel-control-undersized": "1.0.0",
    "carousel-control-not-keyboard": "1.0.0",
    "keyboard-inoperable-control": "1.0.0",
    "submenu-keyboard-inaccessible": "1.0.0",

    # -- focus_obscured --
    "focus-obscured-by-sticky": "1.0.0",
    "focus-obscured-by-fixed": "1.0.0",

    # -- hover_focus --
    "hover-not-dismissible": "1.0.0",
    "hover-disappears-on-hover": "1.0.0",

    # -- error_flow --
    "dynamic-form-error-not-announced": "1.0.0",

    # -- dynamic --
    "dynamic-trigger-not-found": "1.0.0",
    "dynamic-focus-not-moved": "1.0.0",
    "dynamic-attribute-not-set": "1.0.0",
    "dynamic-live-region-silent": "1.0.0",
    "dynamic-error-not-associated": "1.0.0",
    "dynamic-modal-no-focus": "1.0.0",
    "dynamic-modal-focus-outside": "1.0.0",
    "dynamic-modal-no-name": "1.0.0",
    "dynamic-modal-not-trapped": "1.0.0",
    "dynamic-modal-escape-no-close": "1.0.0",

    # -- visual --
    "visual-tiny-text": "1.0.0",
    "visual-marquee-or-blink": "1.0.0",
    "visual-infinite-animation": "1.0.0",
    "visual-carousel-no-pause": "1.0.0",
    "visual-autoplay-sound": "1.0.0",

    # -- responsive --
    "responsive-viewport-meta-missing": "1.0.0",
    "responsive-viewport-zoom-disabled": "1.0.0",
    "responsive-target-size": "1.0.0",

    # -- reflow --
    "reflow-horizontal-scroll": "1.0.0",
    "reflow-overflow-clipped": "1.0.0",
    "reflow-element-exceeds": "1.0.0",

    # -- mobile --
    "mobile-drag-only": "1.0.0",
    "mobile-motion-actuation": "1.0.0",
    "mobile-orientation-locked": "1.0.0",
    "mobile-pointer-gesture": "1.0.0",

    # -- media --
    "media-autoplay": "1.0.0",
    "media-img-decorative-text": "1.0.0",
    "media-img-no-alt": "1.0.0",
    "media-img-placeholder-alt": "1.0.0",
    "media-video-no-track": "1.0.0",

    # -- cognitive --
    "cognitive-duplicate-link-text": "1.0.0",
    "cognitive-empty-link": "1.0.0",
    "cognitive-generic-link-text": "1.0.0",
    "cognitive-reading-level-high": "1.0.0",

    # -- pixels --
    "pixel-contrast-low": "1.0.0",
    "pixel-focus-contrast-low": "1.0.0",
    "pixel-focus-invisible": "1.0.0",

    # -- preferences --
    "preferences-no-forced-colors-query": "1.0.0",
    "preferences-no-reduced-motion-query": "1.0.0",
    "preferences-reduced-motion-ignored": "1.0.0",

    # -- screen_reader --
    "sr-browse-decorative-noise": "1.0.0",
    "sr-browse-skipped-text": "1.0.0",
    "sr-dialog-no-name": "1.0.0",
    "sr-duplicate-landmark": "1.0.0",
    "sr-empty-heading": "1.0.0",
    "sr-label-in-name": "1.0.0",
    "sr-nvda-mismatch": "1.0.0",
    "sr-nvda-silent": "1.0.0",
    "sr-silent-interactive": "1.0.0",

    # -- vlm --
    "vlm-alt-unhelpful": "1.0.0",
    "vlm-error-unclear": "1.0.0",
    "vlm-heading-hierarchy-mismatch": "1.0.0",
    "vlm-link-ambiguous": "1.0.0",
    "vlm-visual-heading-missing": "1.0.0",

    # -- widgets --
    "widget-combobox-missing-controls": "1.0.0",
    "widget-combobox-missing-expanded": "1.0.0",
    "widget-dialog-missing-aria-modal": "1.0.0",
    "widget-dialog-missing-name": "1.0.0",
    "widget-dialog-no-escape": "1.0.0",
    "widget-disclosure-missing-expanded": "1.0.0",
    "widget-tab-missing-controls": "1.0.0",
    "widget-tab-missing-selected": "1.0.0",
    "widget-tablist-no-arrow-nav": "1.0.0",
    "widget-tablist-no-tabs": "1.0.0",
    "widget-visual-tablist-suspect": "1.0.0",
}

# Bump this whenever any rule's version changes. It's the cheap
# "did the rule set change at all?" signal for callers that don't
# want to diff the full dict.
RULE_SET_META_VERSION = "2.1.0"


def version_for(rule_id: str) -> str:
    """Return the registered version for a rule id, or "0.0.0".

    "0.0.0" signals "no entry" — useful for new rules that haven't
    been added to RULE_VERSIONS yet (the test enforces every emitted
    rule has an entry, but defensive code paths shouldn't crash on
    boot when a rule slips through).
    """
    return RULE_VERSIONS.get(rule_id, "0.0.0")


def rule_set_hash() -> str:
    """SHA256 of the sorted (rule_id, version) pairs.

    Stamped at the top of every audit result so a CI gate can prove
    two runs used identical detection logic. Two audits with the same
    hash MUST produce the same set of rule firings on the same DOM.
    """
    canonical = "|".join(
        f"{rid}@{ver}"
        for rid, ver in sorted(RULE_VERSIONS.items())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def register(rule_id: str, version: str) -> None:
    """Register a rule version at runtime — used by plugin packages.

    Plugins discovered via entry-points add their rule IDs through
    this helper so the rule_set_hash includes them.
    """
    RULE_VERSIONS[rule_id] = version
