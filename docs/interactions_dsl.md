# Interaction DSL — dynamic-state testing

Most real accessibility bugs live in **state transitions**: a modal
opens and focus doesn't move into it; a form submits and the error
message isn't announced; an accordion toggles but `aria-expanded` never
updates. Static analysis can't see any of that.

The `interactions` option on an audit request lets you declare, in JSON,
what should happen after a user event — and the audit will fail if the
assertion doesn't hold.

---

## Schema

Each entry in `options.interactions`:

```jsonc
{
  "name": "Open menu",                        // human label, required
  "trigger_selector": "#menu-btn",            // CSS selector, required
  "trigger_action":   "click",                // click | enter | space | escape
  "settle_ms":        300,                    // wait after trigger (default 300)
  "expect": {                                 // at least one expect field
    "focus_moves_to":   "#first-menu-item",
    "attribute_equals": {
      "selector": "#menu-btn",
      "name":     "aria-expanded",
      "value":    "true"
    },
    "live_region_fires":     "#status-bar",
    "error_describes_field": {
      "error_selector": "#email-error",
      "field_selector": "#email"
    }
  }
}
```

All `expect.*` fields are optional. Set more than one and **all** must
pass; the interaction is flagged as broken on the first failing
assertion.

---

## Rules emitted

| Rule | Severity | WCAG | Fires when |
|---|---|---|---|
| `dynamic-trigger-not-found` | moderate | — | `trigger_selector` matches no element |
| `dynamic-focus-not-moved` | serious | 2.4.3 (A) | After trigger, `document.activeElement` doesn't match `focus_moves_to` |
| `dynamic-attribute-not-set` | serious | 4.1.2 (A) | After trigger, `selector` doesn't have `name=value` |
| `dynamic-live-region-silent` | serious | 4.1.3 (AA) | Text didn't change, **or** text changed but element lacks `aria-live` / `role=status\|alert` |
| `dynamic-error-not-associated` | serious | 3.3.1 (A) | After trigger, the field's `aria-describedby` doesn't reference the error message's `id` |

---

## Examples

### Modal dialog — focus must move inside, `aria-expanded` flips

```jsonc
{
  "name": "Open settings dialog",
  "trigger_selector": "#settings-open",
  "trigger_action":   "click",
  "expect": {
    "focus_moves_to":   "#settings-dialog h1",
    "attribute_equals": {
      "selector": "#settings-open",
      "name":     "aria-expanded",
      "value":    "true"
    }
  }
}
```

### Form validation — error announced via live region, linked to field

```jsonc
{
  "name": "Submit empty form",
  "trigger_selector": "#submit",
  "trigger_action":   "click",
  "settle_ms":        500,
  "expect": {
    "live_region_fires": "#form-error-summary",
    "error_describes_field": {
      "error_selector": "#email-error",
      "field_selector": "#email"
    }
  }
}
```

### Disclosure button — `aria-expanded` round-trips

```jsonc
{
  "name": "Toggle FAQ item",
  "trigger_selector": "#faq-item-3 button",
  "trigger_action":   "click",
  "expect": {
    "attribute_equals": {
      "selector": "#faq-item-3 button",
      "name":     "aria-expanded",
      "value":    "true"
    }
  }
}
```

---

## Gotchas

- **Triggers don't reset state between runs.** If you open a menu in
  one interaction and never close it, the next interaction runs
  against the open-menu page. Either declare cleanup interactions
  or audit against a page with the features isolated.
- **`click` simulates a mouse click.** Keyboard-only handlers are
  explicitly NOT tested by `click` — use `trigger_action: "enter"` to
  verify `Enter` fires the click handler on custom `<div role="button">`
  widgets.
- **`settle_ms` is a wall-clock sleep, not a "wait for selector"
  mechanism.** Raise it for slow SPA state transitions if the
  assertion fires before the UI has updated.
- **Focus assertions match via `element.matches(selector)`.** Use a
  narrow selector (ID preferred); otherwise a sibling with the same
  class will falsely pass.
