# What this audit covers (and what it doesn't)

Automated accessibility tooling catches ~30–40% of WCAG barriers. This
document lists what we do and don't check so stakeholders can decide
where to invest manual review effort.

## In scope — automated

| Area | Coverage | Notes |
|---|---|---|
| WCAG 2.2 levels | A, AA, AAA | Level field on every issue; conformance claims in the VPAT. |
| axe-core rules | ~90 rules | Runs as one of our modules (`wcag_engine`). |
| Chromium a11y tree | full walk | Custom rules for silent interactives, empty headings, unnamed dialogs, duplicate landmarks. |
| Keyboard walk | focus mode | 100-stop cap; detects keyboard traps, missing names/focus indicators, positive tabindex, generic focusables. |
| Forms | 4 rules | Missing labels, autocomplete, aria-invalid without describedby, radios without fieldset. |
| Media | 5 rules | Missing/placeholder alt, "image of" noise, video without track, autoplay. |
| Structure | 9 rules | html lang, title, heading hierarchy, main landmark, table headers, iframe titles, lang-of-parts. |
| Visual | 3 rules | `<marquee>`, infinite animation, tiny text. |
| Responsive | 3 rules | Viewport meta, zoom-disabled, target size. |
| Reflow (1.4.10) | 3 rules | 320 CSS-pixel viewport test: horizontal scroll, overflow clipping, elements exceeding. |
| Preferences | 3 rules | `prefers-reduced-motion` / `prefers-contrast` / forced-colors query detection. |
| Cognitive | 3 rules | Non-descriptive link text, duplicate link text, empty links. |
| ARIA | 4 rules | Invalid role, dangling labelledby / describedby, aria-hidden on focusable. |
| Widgets (APG) | 10+ rules | Combobox, dialog, tablist/tab, disclosure — structural + runtime keyboard (ArrowLeft/Right, Escape). Visual-only tablists (no ARIA) flagged as suggestive. |
| Mobile | 4 rules | Orientation lock, drag-only, pointer gestures, motion actuation. |
| Character key shortcuts (2.1.4) | 2 rules | Single-character `accesskey` + unguarded single-key inline handlers. Turn-off/remap/focus-scope still needs manual review. |
| Timing adjustable (2.2.1) | 2 rules | Client-side `<meta http-equiv="refresh">` time limits / timed redirects. Server-side session timeouts out of scope. |
| Reflow | 3 rules | 320 CSS pixel viewport: horizontal scroll, clip, element overflow. |
| Pixel analysis (opt-in) | 3 rules | Measured contrast from screenshots (1.4.3), focus-indicator contrast (2.4.11/2.4.13), invisible focus (2.4.7). |
| Shadow DOM | all modules | Keyboard walks, widgets, and structure traverse `element.shadowRoot` trees. |
| YAML rules (opt-in) | unlimited | Team-authored DOM-pattern rules loaded from a YAML file. |
| Screen reader (Path A, no NVDA) | 5 rules | Silent interactive, empty heading, dialog without name, duplicate landmarks, Label-in-Name (2.5.3). |
| Screen reader (Path B, real NVDA) | 4 rules | `sr-nvda-silent`, `sr-nvda-mismatch`, `sr-browse-skipped-text`, `sr-browse-decorative-noise`. |
| Dynamic state (DSL) | 5 rules | Focus moves, attribute round-trip, live region fires, error field association. |

## In scope — when configured

- **Auth-gated audits** via `login` option (url + selectors + creds).
- **Multi-page audits** via `urls: ["/a", "/b"]` — results aggregated.
- **Dynamic interactions** via the [interactions DSL](interactions_dsl.md).
- **AI-enriched reports** via OpenRouter: adds `location_guide`,
  `reproduction_steps`, `recommendation`, `user_impact` per issue.

## Out of scope — known limitations

| Gap | Why | Workaround |
|---|---|---|
| JAWS verbosity / heuristics | Commercial license required (~$95/yr home, $1200/yr pro). Our NVDA-only Path B misses JAWS-specific quirks. | Manual testing on a JAWS-licensed workstation, especially for enterprise targets. |
| VoiceOver (macOS/iOS) | No macOS worker; macOS-only SR. | Manual testing on Apple devices. |
| TalkBack (Android) | Touch-navigation model, different device. | Manual mobile testing. |
| PDF / Office documents | Different file format, different a11y model. | Use Adobe Acrobat Pro or PAC (PDF Accessibility Checker). |
| Video caption quality | We detect presence of `<track>`, not whether captions are accurate/complete. | Manual review. |
| Alt-text usefulness | We flag empty / placeholder / "image of" alts, not whether a non-empty alt actually describes the image. | Human review — this is content QA, not code QA. |
| Cognitive accessibility 3.1.3–3.1.6 | Reading level / unusual words / abbreviations are subjective. | Manual review with the audience in mind. |
| Color contrast for overlays / gradient text | axe-core contrast only sees solid backgrounds. | Manual contrast check with Colour Contrast Analyser. |
| Focus indicator contrast (2.4.11/2.4.13) | We detect presence, not contrast ratio of the focus ring. | Manual review. |
| Real user feedback | No amount of automation replaces testing with disabled participants. | Engage users from the disability community. |

## Honest numbers

- A perfect score on this audit is NOT a WCAG conformance claim on its
  own. It means the automated rules found nothing. "Not Evaluated" rows
  in the VPAT sheet show where manual work remains.
- The `by_level` rollup is calibrated to be pessimistic: an issue
  mapping to BOTH a level-A and a level-AA SC is counted at A because
  failing A blocks any conformance claim.
- When Path B (real NVDA) couldn't run (no Windows worker, OS focus
  stolen by another window), the audit surfaces that state explicitly
  with `nvda_status` and a `skip_reason` — it doesn't silently pretend
  the SR-only rules passed.
