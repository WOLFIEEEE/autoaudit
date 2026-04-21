"""User-preference module — tests how the page behaves under
`prefers-reduced-motion` and `forced-colors` emulation.

Covers two real-world failure modes automated tools often miss:

- `preferences-reduced-motion-ignored`  WCAG 2.3.3 serious
  Under `prefers-reduced-motion: reduce`, infinite or long CSS
  animations keep playing. Users with vestibular disorders ask the OS
  to suppress motion; if the site doesn't honor the media query,
  content still moves.

- `preferences-no-forced-colors-query`  WCAG 1.4.8 moderate
  The stylesheet contains no `@media (forced-colors: active)` rule.
  In Windows High Contrast mode the browser applies its own palette;
  author styles that don't opt in via `forced-color-adjust` often
  become unreadable (invisible borders, identical bg/fg).

Both checks are light-weight page.evaluate() calls. We don't compare
screenshots — that belongs to a heavier interactive pass.

Implementation notes:
- We emulate `prefers-reduced-motion: reduce` only briefly, check the
  computed animationPlayState, then reset. Emulation is scoped to the
  browser context; if we leave it on it would bleed into subsequent
  modules.
- Forced-colors detection is static (walk stylesheets) because
  `page.emulate_media(forced_colors="active")` requires a Chromium
  feature flag that's not universally enabled. The static check
  catches the important case: pages that have no awareness at all.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)


# The JS we run inside the page. Single function body — Playwright
# converts `() => {...}` to evaluate().
_JS_DETECT = r"""
() => {
  // Does any loaded stylesheet have @media (prefers-reduced-motion) or
  // @media (forced-colors) rules? We walk rules defensively — cross-origin
  // stylesheets throw on cssRules access.
  let hasReducedMotionQuery = false;
  let hasForcedColorsQuery = false;

  for (const sheet of document.styleSheets) {
    let rules;
    try {
      rules = sheet.cssRules;
    } catch (e) {
      continue; // cross-origin, skip
    }
    if (!rules) continue;
    for (const rule of rules) {
      if (rule.type === CSSRule.MEDIA_RULE) {
        const c = (rule.conditionText || '').toLowerCase();
        if (c.includes('prefers-reduced-motion')) hasReducedMotionQuery = true;
        if (c.includes('forced-colors')) hasForcedColorsQuery = true;
      }
    }
  }

  // With prefers-reduced-motion emulated to "reduce", find elements still
  // animating. We care about either CSS animations with non-zero duration
  // or CSS transitions with non-zero duration.
  const stillAnimating = [];
  const all = document.querySelectorAll('*');
  for (let i = 0; i < all.length && stillAnimating.length < 10; i++) {
    const el = all[i];
    const cs = getComputedStyle(el);
    const hasAnim = cs.animationName &&
                    cs.animationName !== 'none' &&
                    parseFloat(cs.animationDuration) > 0.01 &&
                    cs.animationPlayState !== 'paused';
    if (hasAnim) {
      stillAnimating.push({
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        cls: (el.className && typeof el.className === 'string')
              ? el.className.slice(0, 80)
              : '',
        animation: cs.animationName,
        duration: cs.animationDuration,
        iterations: cs.animationIterationCount,
      });
    }
  }

  return {
    hasReducedMotionQuery,
    hasForcedColorsQuery,
    stillAnimating,
  };
}
"""


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    issues: list[dict[str, Any]] = []
    details: dict[str, Any] = {}

    try:
        # Emulate reduced-motion for the duration of the detection.
        # Reset afterwards so subsequent modules see the user-provided
        # state (typically: no preference).
        page.emulate_media(reduced_motion="reduce")
    except Exception as exc:
        log.debug("emulate_media(reduced_motion) failed: %s", exc)

    try:
        probe = page.evaluate(_JS_DETECT)
    except Exception as exc:
        log.exception("preferences probe failed")
        # Reset before returning.
        try:
            page.emulate_media(reduced_motion="no-preference")
        except Exception:
            pass
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    finally:
        try:
            page.emulate_media(reduced_motion="no-preference")
        except Exception as exc:
            log.debug("reset emulate_media failed: %s", exc)

    details = probe
    if not probe.get("hasReducedMotionQuery"):
        issues.append(
            make_issue(
                issue_id="preferences-no-reduced-motion-query",
                module="preferences",
                rule="preferences-no-reduced-motion-query",
                severity="moderate",
                wcag=["2.3.3"],
                title="No @media (prefers-reduced-motion) support detected",
                description=(
                    "The page's stylesheets contain no rules that adapt to the "
                    "user's `prefers-reduced-motion` OS setting. Users with "
                    "vestibular disorders who request reduced motion will still "
                    "see every animation at full amplitude."
                ),
                selector="stylesheet",
                details={},
                fix=(
                    "Add a stylesheet block:\n"
                    "  @media (prefers-reduced-motion: reduce) {\n"
                    "    *, *::before, *::after {\n"
                    "      animation-duration: 0.01ms !important;\n"
                    "      animation-iteration-count: 1 !important;\n"
                    "      transition-duration: 0.01ms !important;\n"
                    "    }\n"
                    "  }"
                ),
            )
        )

    still = probe.get("stillAnimating") or []
    if still:
        issues.append(
            make_issue(
                issue_id="preferences-reduced-motion-ignored",
                module="preferences",
                rule="preferences-reduced-motion-ignored",
                severity="serious",
                wcag=["2.3.3"],
                title=f"{len(still)} element(s) keep animating under prefers-reduced-motion",
                description=(
                    "With `prefers-reduced-motion: reduce` emulated, the page "
                    "still runs CSS animations. Users who requested reduced "
                    "motion at the OS level will experience the full motion."
                ),
                selector=_selector_hint(still[0]),
                details={"offenders": still},
                fix=(
                    "Gate animations behind `@media (prefers-reduced-motion: "
                    "no-preference)`, or set `animation-duration: 0` inside "
                    "a `@media (prefers-reduced-motion: reduce)` block."
                ),
            )
        )

    if not probe.get("hasForcedColorsQuery"):
        issues.append(
            make_issue(
                issue_id="preferences-no-forced-colors-query",
                module="preferences",
                rule="preferences-no-forced-colors-query",
                severity="moderate",
                wcag=["1.4.8"],
                title="No @media (forced-colors: active) support detected",
                description=(
                    "The page has no styles scoped to `forced-colors: active`. "
                    "In Windows High Contrast mode (and macOS/iOS increased "
                    "contrast) the browser overrides colors using system "
                    "palette; author styles often lose semantic distinctions "
                    "(e.g. disabled vs. active buttons become identical)."
                ),
                selector="stylesheet",
                details={},
                fix=(
                    "Test the page with Windows High Contrast or Chrome's "
                    "`Emulate CSS media feature forced-colors` devtool. Add "
                    "`@media (forced-colors: active)` rules that use system "
                    "colors (`CanvasText`, `Highlight`, `ButtonText`, etc.) "
                    "or `forced-color-adjust: none` where custom colors are "
                    "semantically required."
                ),
            )
        )

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "details": details,
    }


def _selector_hint(offender: dict[str, Any]) -> str:
    """Build a CSS-ish selector from the JS offender dict."""
    tag = offender.get("tag") or ""
    id_ = offender.get("id") or ""
    cls = (offender.get("cls") or "").split()
    parts = [tag]
    if id_:
        parts.append(f"#{id_}")
    for c in cls[:2]:
        parts.append(f".{c}")
    return "".join(parts) or "element"
