# Rule catalog

Every custom rule this server produces, grouped by module. Every entry lists: rule ID, WCAG success criteria, severity, what it catches, and a fix hint.

The `wcag_engine` module additionally emits every axe-core rule matching the configured level tags (`wcag2a`, `wcag2aa`, `wcag22aa`, …). Those rules are not listed here — see the [axe-core rule documentation](https://github.com/dequelabs/axe-core/blob/master/doc/rule-descriptions.md) for the authoritative list.

- [Severity model](#severity-model)
- [structure](#structure)
- [aria](#aria)
- [media](#media)
- [cognitive](#cognitive)
- [visual](#visual)
- [keyboard](#keyboard)
- [forms](#forms)
- [responsive](#responsive)
- [screen_reader](#screen_reader)

---

## Severity model

| Severity  | Points deducted from score | Use when… |
|-----------|----------------------------|-----------|
| critical  | 8 | Blocks access entirely for one or more user groups (e.g. an unlabeled button; a keyboard trap). |
| serious   | 4 | Significantly degrades the experience but does not block it (e.g. missing focus indicator). |
| moderate  | 2 | Breaks a best practice or a specific WCAG criterion that most users can work around. |
| minor     | 1 | Edge-case failures or best-practice warnings (e.g. 7px text). |

Scores start at 100 and are floored at 0. See [the Summary schema in docs/api.md](api.md#summary) for the mapping from score to grade.

---

## structure

Document outline: language declaration, title, headings, landmarks, tables.

### `structure-html-lang`

- **WCAG:** 3.1.1 (Language of Page)
- **Severity:** serious
- **Principle:** understandable

The `<html>` element is missing a `lang` attribute. Screen readers use `lang` to select pronunciation rules; without it, English content may be read with Spanish phonetics (or vice versa).

**Fix:** `<html lang="en">` (or your content's language).

### `structure-title-missing`

- **WCAG:** 2.4.2 (Page Titled)
- **Severity:** serious
- **Principle:** operable

The `<title>` element is missing or empty. Title announcements orient screen-reader users when the page loads.

**Fix:** Add a descriptive `<title>` inside `<head>`.

### `structure-no-h1`

- **WCAG:** 1.3.1 (Info and Relationships)
- **Severity:** moderate
- **Principle:** perceivable

The page has no `<h1>`. Users navigating by heading lose the top-level landmark.

**Fix:** Add one `<h1>` describing the page's primary content.

### `structure-multiple-h1`

- **WCAG:** 1.3.1
- **Severity:** minor
- **Principle:** perceivable

Multiple `<h1>` elements found. The HTML5 outline algorithm was never implemented by browsers, so multiple h1s appear as peer top-level entries in heading navigation.

**Fix:** Keep a single `<h1>`; demote extras to `<h2>`.

### `structure-heading-skip`

- **WCAG:** 1.3.1
- **Severity:** moderate
- **Principle:** perceivable

A heading level jumps by more than one (e.g. `<h2>` followed by `<h4>`). Screen-reader heading navigation uses levels to communicate document depth; skips confuse the outline.

**Details:** `{ from_level, to_level }`.

**Fix:** Use the next level down, or introduce an intermediate heading.

### `structure-no-main`

- **WCAG:** 1.3.1
- **Severity:** moderate
- **Principle:** perceivable

No `<main>` element and no `role="main"` landmark. Screen-reader users cannot jump directly to the primary content.

**Fix:** Wrap the primary content in `<main>` or add `role="main"`.

### `structure-table-no-th`

- **WCAG:** 1.3.1
- **Severity:** serious
- **Principle:** perceivable

A `<table>` has no `<th>` cells. Without header cells the table is announced as a flat grid, losing row/column context.

**Fix:** Mark header cells with `<th>` and use `scope="col"` / `scope="row"`.

---

## aria

ARIA role validity and reference integrity.

### `aria-invalid-role`

- **WCAG:** 4.1.2 (Name, Role, Value)
- **Severity:** serious
- **Principle:** robust

The `role` attribute contains no valid ARIA 1.2 role. Assistive tech ignores unknown roles; the element falls back to its native semantics, which may not match intent. Space-separated fallback roles are allowed — the rule fires only when no token is valid.

**Details:** `{ role }` (the raw attribute value).

**Fix:** Use a valid role, or remove the attribute to rely on native semantics.

### `aria-labelledby-missing`

- **WCAG:** 4.1.2
- **Severity:** serious
- **Principle:** robust

`aria-labelledby` references an ID that doesn't exist in the document. The control has no accessible name at all when the referent is missing.

**Details:** `{ missing_ids: string[] }`.

**Fix:** Ensure each ID in `aria-labelledby` matches an existing element's `id`.

### `aria-describedby-missing`

- **WCAG:** 4.1.2
- **Severity:** moderate
- **Principle:** robust

`aria-describedby` references an ID that doesn't exist. The accessible description will be empty.

**Details:** `{ missing_ids: string[] }`.

**Fix:** Ensure each ID in `aria-describedby` matches an existing element's `id`.

### `aria-hidden-focusable`

- **WCAG:** 4.1.2
- **Severity:** serious
- **Principle:** robust

`aria-hidden="true"` on a focusable element, or an element with focusable descendants. Keyboard users can tab to an invisible control that screen readers ignore — a worst-of-both-worlds trap.

**Details:** `{ focusable_child: bool }`.

**Fix:** Remove `aria-hidden`, or remove the element from the tab order (`tabindex="-1"`).

---

## media

Images, video, audio.

### `media-img-no-alt`

- **WCAG:** 1.1.1 (Non-text Content)
- **Severity:** critical
- **Principle:** perceivable

`<img>` has no `alt` attribute and is not marked decorative (`role="presentation"`, `role="none"`, or `aria-hidden="true"`). Screen readers announce the raw file path or nothing.

**Fix:** `alt="describe the image"` or `alt=""` for decorative images.

### `media-img-placeholder-alt`

- **WCAG:** 1.1.1
- **Severity:** moderate
- **Principle:** perceivable

Alt text matches a placeholder or filename pattern: `image`, `photo`, `img`, `icon`, `logo`, `IMG_1234`, `DSC_0042`, or ends in `.jpg` / `.png` / `.gif` / `.webp` / `.svg` / `.bmp`.

**Details:** `{ alt, src }`.

**Fix:** Replace with a short description of the image's purpose.

### `media-img-decorative-text`

- **WCAG:** 1.1.1
- **Severity:** minor
- **Principle:** perceivable

An image marked `role="presentation"`, `role="none"`, or `aria-hidden="true"` has non-empty `alt`. The two signals conflict — some screen readers honor the role and skip, others read the alt.

**Details:** `{ alt, role }`.

**Fix:** Use `alt=""` on decorative images.

### `media-video-no-track`

- **WCAG:** 1.2.2 (Captions — Prerecorded)
- **Severity:** serious
- **Principle:** perceivable

A `<video>` has no `<track kind="captions">` or `<track kind="subtitles">` child.

**Fix:** Add `<track kind="captions" srclang="en" src="captions.vtt">` inside `<video>`.

### `media-autoplay`

- **WCAG:** 1.4.2 (Audio Control)
- **Severity:** serious
- **Principle:** operable

A `<video>` or `<audio>` has `autoplay` without `muted`. Auto-playing audio interferes with screen readers.

**Fix:** Remove `autoplay`, add `muted`, or provide a visible pause control.

---

## cognitive

Comprehension and link-list quality.

### `cognitive-empty-link`

- **WCAG:** 2.4.4 (Link Purpose — In Context)
- **Severity:** serious
- **Principle:** understandable

A link has no accessible name — no visible text, no `aria-label`, no nested `<img alt>`, no `title`. Announced as `"link"` with no indication of purpose.

**Details:** `{ href }`.

**Fix:** Add visible text, `aria-label`, or an image with descriptive alt.

### `cognitive-generic-link-text`

- **WCAG:** 2.4.4
- **Severity:** moderate
- **Principle:** understandable

Link text is a generic phrase: `click here`, `click`, `here`, `read more`, `more`, `learn more`, `details`, `info`, `link`, `this`, `this link`, `this page`.

**Details:** `{ href }`.

**Fix:** Rewrite the text to describe the destination.

### `cognitive-duplicate-link-text`

- **WCAG:** 2.4.4
- **Severity:** moderate
- **Principle:** understandable

Two or more links share identical text but point to different URLs. In the screen-reader link list, they're indistinguishable.

**Details:** `{ text, distinct_urls: string[] }`.

**Fix:** Differentiate the text (e.g. "Read the 2025 report" vs "Read the 2024 report").

---

## visual

Static rules. Contrast is handled by the axe-core engine, not duplicated here.

### `visual-marquee-or-blink`

- **WCAG:** 2.2.2 (Pause, Stop, Hide)
- **Severity:** serious
- **Principle:** operable

A `<marquee>` or `<blink>` element is present. These deprecated elements animate without a built-in pause mechanism.

**Fix:** Replace with a pausable CSS animation or a component with explicit pause/stop controls.

### `visual-infinite-animation`

- **WCAG:** 2.2.2
- **Severity:** moderate
- **Principle:** operable

An element has `animation-iteration-count: infinite` and `animation-duration >= 0.5s`. Auto-starting animations longer than 5 seconds must have a pause/stop/hide control; infinite always qualifies.

The 0.5s floor filters out micro-interactions (hover pulses, short fades) that 2.2.2 doesn't target.

**Details:** `{ animation_name, duration_s, iteration_count }`.

**Fix:** Provide a pause control, respect `prefers-reduced-motion`, or make the animation finite.

### `visual-tiny-text`

- **WCAG:** 1.4.4 (Resize Text)
- **Severity:** minor
- **Principle:** perceivable

Visible text with computed `font-size < 9px`. Often indicates an absolute pixel size that doesn't scale with browser zoom.

**Details:** `{ font_size_px, tag }`.

**Fix:** Use at least 12px for body text, or a relative unit (`rem` / `em` / `%`).

---

## keyboard

Driven by a real Tab walk: we press Tab up to `max_tabs` times and record focus state at each stop.

### `keyboard-trap-suspected`

- **WCAG:** 2.1.2 (No Keyboard Trap)
- **Severity:** critical
- **Principle:** operable

Tab was pressed `max_tabs` times and focus never wrapped to the first element or left the page. Users relying on keyboard navigation may be stuck.

**Details:** `{ tab_stops_observed, max_tabs }`.

**Fix:** Verify every component lets Tab move focus out. Modals should trap focus only while open and release on close.

### `keyboard-no-focus-indicator`

- **WCAG:** 2.4.7 (Focus Visible)
- **Severity:** serious
- **Principle:** operable

The focused element has `outline: none` and no replacement box-shadow or border change. Sighted keyboard users cannot see where focus is.

**Details:** `{ outline_style, box_shadow }`.

**Fix:** Add a `:focus` or `:focus-visible` style with visible outline, box-shadow, or border change.

### `keyboard-no-accessible-name`

- **WCAG:** 4.1.2
- **Severity:** critical
- **Principle:** robust

When focused, the element has no accessible name resolvable via `aria-label`, `aria-labelledby` (with real target), `<label for>`, wrapping `<label>`, visible text, nested `<img alt>`, `placeholder`, or `title`.

**Details:** `{ tag, role, tab_index }`.

**Fix:** Add visible text, `aria-label`, or a `<label for>` association.

### `keyboard-positive-tabindex`

- **WCAG:** 2.4.3 (Focus Order)
- **Severity:** moderate
- **Principle:** operable

The focused element has `tabindex > 0`. Positive tabindex overrides the DOM order and almost always surprises users.

**Details:** `{ tabindex }`.

**Fix:** Use `tabindex="0"` (or no tabindex) and let DOM order determine focus order.

### `keyboard-generic-focusable`

- **WCAG:** 4.1.2
- **Severity:** serious
- **Principle:** robust

The focused element has a non-semantic tag (`div`, `span`, `p`, …) and no explicit `role` attribute. Screen readers can't tell users what kind of control it is — the classic `<div onclick>` / `<span tabindex>` anti-pattern.

**Details:** `{ tag, tab_index }`.

**Fix:** Use a semantic element (`<button>`, `<a>`) or add an appropriate role plus keyboard handlers.

---

## forms

Static form checks. The plan's interactive "submit empty and capture errors" flow is deferred.

### `forms-input-no-label`

- **WCAG:** 3.3.2 (Labels or Instructions), 4.1.2
- **Severity:** critical
- **Principle:** understandable

A form control (`<input>`, `<select>`, `<textarea>`, excluding `type=submit|reset|button|hidden|image`) has no accessible label. Screen-reader users hear only the field type.

**Details:** `{ tag, type, name }`.

**Fix:** `<label for="fieldid">Description</label>` (preferred) or `aria-label`.

### `forms-radio-group-no-fieldset`

- **WCAG:** 1.3.1
- **Severity:** serious
- **Principle:** perceivable

Two or more radios (or checkboxes) share a `name` but aren't wrapped in `<fieldset><legend>` and don't have an ancestor with `role="radiogroup"` or `role="group"`. Screen-reader users can't tell the options are part of the same question.

**Details:** `{ name, type, option_count }`.

**Fix:** Wrap in `<fieldset><legend>Question text</legend>…</fieldset>`.

### `forms-aria-invalid-no-description`

- **WCAG:** 3.3.1 (Error Identification), 3.3.3 (Error Suggestion)
- **Severity:** moderate
- **Principle:** understandable

A field has `aria-invalid="true"` but `aria-describedby` is missing or points at a nonexistent ID. Screen readers announce the field as invalid with no explanation.

**Details:** `{ aria_describedby }`.

**Fix:** Point `aria-describedby` at an element containing the error text.

### `forms-missing-autocomplete`

- **WCAG:** 1.3.5 (Identify Input Purpose)
- **Severity:** minor
- **Principle:** understandable

A field that appears to collect personal data has no `autocomplete` attribute. Triggered when:

- `type` is `email`, `tel`, or `password`, or
- `name` / `id` contains `email`, `phone`, `tel`, `fname`, `lname`, `fullname`, `address`, `street`, `city`, `zip`, `postal`, `country`.

**Details:** `{ type, name }`.

**Fix:** `autocomplete="email"` / `"tel"` / etc. See the [HTML autocomplete token list](https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#autofill).

---

## responsive

### `responsive-viewport-meta-missing`

- **WCAG:** 1.4.4, 1.4.10 (Reflow)
- **Severity:** serious
- **Principle:** perceivable

The page has no `<meta name="viewport">`. On mobile, the page renders at desktop width and is scaled down, defeating both zoom and reflow.

**Fix:** `<meta name="viewport" content="width=device-width, initial-scale=1">`.

### `responsive-viewport-zoom-disabled`

- **WCAG:** 1.4.4
- **Severity:** serious
- **Principle:** perceivable

The viewport meta has `user-scalable=no` or `maximum-scale < 2.0`. Prevents users with low vision from zooming text to a readable size.

**Details:** `{ content }`.

**Fix:** Remove `user-scalable=no` and raise `maximum-scale` to 2.0 or omit it.

### `responsive-target-size`

- **WCAG:** 2.5.8 (Target Size — Minimum) — new in WCAG 2.2 AA
- **Severity:** moderate
- **Principle:** operable

An interactive target (`<a href>`, `<button>`, non-hidden `<input>`, `<select>`, `<textarea>`, elements with `role=button|link|checkbox|radio|switch|tab|menuitem`) is smaller than 24×24 CSS pixels.

Exceptions that do NOT fire the rule:

- Inline targets in flowing text (`display: inline`) per the 2.5.8 inline exception.
- Hidden / disabled controls.
- Offscreen (0×0) elements.

**Details:** `{ width, height, tag, role, type }`.

**Fix:** Increase padding or minimum size to at least 24×24px.

---

## screen_reader

Path A (this file): Chromium a11y-tree analysis via `page.accessibility.snapshot()`. Cross-platform. Four rules.

Path B (deferred): real NVDA speech capture on a Windows worker. Would stack additional rules on top; dedup merges overlaps.

### `sr-silent-interactive`

- **WCAG:** 4.1.2
- **Severity:** critical
- **Principle:** robust

A node with interactive role (`button`, `link`, `checkbox`, `radio`, `textbox`, `searchbox`, `combobox`, `menuitem`, `menuitemcheckbox`, `menuitemradio`, `switch`, `tab`, `treeitem`, `option`, `slider`, `spinbutton`) has no accessible name and is not disabled.

Overlaps with `keyboard-no-accessible-name` for focusable elements; the deduplicator does NOT collapse them because they use different keying (Chromium tree node vs CSS selector). Users may see both reports for the same underlying element.

**Details:** `{ role, tree_name }`.

**Fix:** Add visible text, `aria-label`, `aria-labelledby`, or a `<label for>` association.

### `sr-empty-heading`

- **WCAG:** 1.3.1
- **Severity:** serious
- **Principle:** perceivable

A heading node has no accessible name. Screen-reader heading lists show a blank entry and the document outline breaks.

**Details:** `{ level }`.

**Fix:** Remove the empty heading or add text content.

### `sr-dialog-no-name`

- **WCAG:** 4.1.2
- **Severity:** serious
- **Principle:** robust

A `dialog` or `alertdialog` has no accessible name. When it opens, users hear `"dialog"` with no indication of purpose.

**Details:** `{ role }`.

**Fix:** Add `aria-label` or `aria-labelledby` pointing to the dialog title.

### `sr-duplicate-landmark`

- **WCAG:** 1.3.1
- **Severity:** moderate
- **Principle:** perceivable

Two or more landmarks of the same role share a name (or both are empty). In the screen-reader landmark list they are indistinguishable.

Only the 2nd (and 3rd, …) occurrences are reported — the first is the "canonical".

**Details:** `{ role, shared_name, count }`.

**Fix:** Give each landmark a distinct `aria-label` (e.g. `"Primary"` vs `"Footer"`).
