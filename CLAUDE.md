# autoaudit

FastAPI + Celery + Playwright service that audits web pages for WCAG 2.2
barriers. axe-core is one module among ~50; most rules are our own.

## Layout

| Path | What |
|---|---|
| `audit/` | Rule modules. One concern per file; `orchestrator.py` runs them all and aggregates. |
| `server/` | FastAPI app, SQLite persistence, Celery tasks, middleware. |
| `benchmarks/` | Precision/recall harness. `corpus/<fixture>/{page.html,ground_truth.yaml}`. |
| `scripts/` | CLI, report/diff tools, NVDA smoke tests, Windows worker setup. |
| `docs/` | Architecture, API, configuration, coverage, Windows worker. |

## Non-obvious constraints

**Path A vs Path B.** Path A analyses Chromium's accessibility tree and
runs anywhere. Path B drives a *real NVDA* and is Windows-only, routed
to a separate Celery `nvda` queue. A Linux worker saves the audit with
`nvda_status="pending"`; a Windows worker fills it in later.

**NVDA needs the foreground.** NVDA only reads the focused window, so
Path B runs headful, one job at a time, and nothing else may steal focus
mid-run. This is not a bug to fix — it is how screen readers work.

**Browse mode needs OS-level keys.** NVDA implements browse-mode
navigation inside a low-level Windows keyboard hook. Playwright's
`page.keyboard.press()` injects over CDP and never reaches that hook, so
arrow keys silently do nothing. Browse-mode keys go through `SendInput`
(`audit/_win_input.py`). Tab is the exception — it changes focus in
Blink, which fires a real AX event NVDA sees regardless.

**NVDA speech comes from its log.** There is no NVDA add-on. We launch
NVDA at `--log-level=12` and parse its `Speaking [...]` entries. That
format is an internal NVDA detail with no stability contract, and it
fails *silently* (empty transcript, no exception).
`tests/test_nvda_log_canary.py` pins it — if that test breaks after an
NVDA upgrade, re-capture the fixture rather than loosening the parser.

## Rules for writing rules

**Never let a degraded run become a finding.** The worst failure mode in
this codebase is a confident false positive: it destroys trust in the
whole tool far faster than a miss does. If an input is missing or a
capture came back empty, you cannot distinguish "the page is broken"
from "we failed to measure". Emit nothing and set `skipped_analysis` +
`skip_reason` so the report says so out loud. See
`analyze_browse_mode` and `NVDAController.analyze_results` for the shape.

**Issue IDs must be deterministic.** Python salts `hash()` for str per
process, so `f"rule-{hash(text):x}"` produces a different id on every
run and any consumer keying on it re-creates the same finding as new.
Use `stable_short_id()` / `issue_fingerprint()` from `audit/_fingerprint.py`.

**Every new rule needs a corpus fixture.** Add a positive case to
`benchmarks/corpus/` with `expected[]`, and add the rule to the
clean-page fixtures' `forbidden[]` so a false positive on a clean page
fails the benchmark. `python scripts/fixture_coverage.py` reports where
the gaps are and gates CI on a ratchet that only ever rises.

**Confidence is measured, not asserted.** `audit/confidence.py` caps a
rule's reported confidence at what `benchmarks/precision.json` supports;
an unmeasured rule can never be `high`. It is opt-in
(`options["confidence_tiering"]`) until positive corpus coverage is broad
enough that an unmeasured rule is the exception — confidence is a score
multiplier, so switching it on early would inflate every score.

**The coverage map must match the code.** `wcag_coverage.py` feeds the
VPAT. A criterion with a shipping rule may never be `manual_only`, or the
VPAT reports "Not Evaluated" for something we do evaluate.
`tests/test_coverage_map_integrity.py` derives this from the
`make_issue` call sites and enforces it.

## Commands

```bash
pytest -m "not slow" -q          # unit + API
pytest -m slow -q                # Playwright e2e
python benchmarks/run_benchmark.py   # precision / recall vs the corpus
ruff check audit server tests scripts benchmarks
python scripts/gen_rules_reference.py --check   # docs/rules.md is generated
```

Windows only, and they speak out loud:

```bash
python scripts/smoke_test_nvda.py
python scripts/verify_nvda_rules.py
```

## Security posture

- Target URLs are SSRF-checked (`server/models.py`) — resolved, then
  private/loopback/reserved addresses rejected.
- `API_KEYS` enables auth. With it unset the server **fails closed**
  (503) unless `ALLOW_ANONYMOUS=1`.
- Audits are scoped to the API key that created them. Rows predating the
  `owner_key_id` column have a NULL owner and stay readable by any
  authenticated caller.
