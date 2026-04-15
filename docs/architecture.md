# Architecture

How the pieces fit together, why they're split that way, and where the sharp edges are.

- [Component overview](#component-overview)
- [Request lifecycle](#request-lifecycle)
- [Audit pipeline phases](#audit-pipeline-phases)
- [Module pattern: analyze vs run](#module-pattern-analyze-vs-run)
- [Deduplication strategy](#deduplication-strategy)
- [Path A vs Path B: the screen-reader module](#path-a-vs-path-b-the-screen-reader-module)
- [Why static modules run sequentially](#why-static-modules-run-sequentially)

---

## Component overview

```
                         ┌─────────────────┐
                         │   HTTP client    │
                         │  POST /audit     │
                         └────────┬────────┘
                                  │
                                  ▼
                         ┌─────────────────┐
                         │    FastAPI       │       server/app.py
                         │    server        │       • validates URL
                         │                  │       • checks cache
                         └────────┬─────────┘       • enqueues job
                    ┌─────────────┼──────────────┐
                    ▼             ▼              ▼
              ┌──────────┐  ┌──────────┐  ┌──────────┐
              │  Redis   │  │  Redis   │  │ SQLite   │
              │  broker  │  │  cache   │  │  store   │       server/database.py
              │ (Celery) │  │ (optional)│  │          │
              └────┬─────┘  └──────────┘  └──────────┘
                   │
                   ▼
         ┌────────────────────┐                   celery_app.py
         │   Celery worker    │                   server/tasks.py
         │                    │
         │  AuditOrchestrator │                   audit/orchestrator.py
         │      ├── browser  ─┼── Playwright ──── audit/browser.py
         │      ├── wcag_engine ──── axe-core ─── audit/wcag_engine.py
         │      ├── structure, aria, media,
         │      │   cognitive, visual, keyboard,
         │      │   forms, responsive, screen_reader
         │      │     (nine custom modules)
         │      ├── scorer    ────────────────── audit/scorer.py
         │      └── deduplicator ─────────────── audit/deduplicator.py
         └────────────────────┘
```

Everything the worker needs (Playwright, axe, the modules) is regular Python — no IPC, no file watches, no signal gymnastics. The queue + DB boundary is the only network hop.

---

## Request lifecycle

### `POST /audit` (queued)

1. FastAPI validates the request against `AuditRequest` (Pydantic v2).
2. Cache check: hash the URL, look up in Redis. Hit → return cached result with `status: "completed"`.
3. Miss: create a UUID `job_id`, insert a row into SQLite with `status: "queued"`.
4. Enqueue a Celery task carrying `{job_id, url, options}`.
5. Return `200 {job_id, status: "queued", estimated_seconds: 60, poll_url}` to the client.

The worker picks up the task, updates status to `running`, runs `AuditOrchestrator`, writes the result back to SQLite, and sets the Redis cache.

### `POST /audit/quick` (synchronous)

1. FastAPI validates the request.
2. Calls `run_quick_audit(url, options)` inline in the request thread.
3. Launches Playwright, navigates, injects axe-core, runs once, scores the result.
4. Returns the result directly.

No Redis, no SQLite, no job_id. Suitable for request-response use cases (CI smoke checks, webhooks, IDE integrations) that can tolerate up to ~10 seconds of synchronous wait.

---

## Audit pipeline phases

From `AuditOrchestrator.run()`:

```
Phase 1  Setup          Launch browser, navigate to URL, wait for networkidle.
                        ~2 s.

Phase 2  Static         wcag_engine (axe-core)
         analysis       structure
         (sequential)   aria
                        media
                        cognitive
                        Each is a single page.evaluate() — total ~0.5-2 s.

Phase 3  Visual &       visual
         responsive     responsive
                        Both are static but live here to keep Phase 2 purely
                        semantic. ~0.1 s.

Phase 4  Keyboard       Real Tab walk driven by the worker.
                        ~5-30 s depending on max_tabs and page size.

Phase 5  Forms          Static. ~0.1 s.

Phase 6  Screen reader  Path A (a11y tree snapshot) always runs.
                        Path B (real NVDA) stacks on top when available.
                        ~0.1 s (Path A); ~15-40 s when Path B runs.

Phase 7  Aggregate      Collect all issues, deduplicate, score,
                        format module summaries.
                        ~0.05 s.
```

Total wall-clock for a real-world page: 10–60 seconds. The integration test against a small fixture completes in ~5 seconds.

---

## Module pattern: analyze vs run

Every custom audit module splits into two functions:

```python
# audit/<name>.py

_EXTRACT_JS = """() => { /* DOM query */ }"""

def analyze(data: dict) -> list[dict]:
    """Pure Python. Takes the extractor's output, returns issues."""
    ...

def run(page, options=None) -> dict:
    """Thin wrapper. page.evaluate(_EXTRACT_JS) -> analyze() -> module result."""
    start = time.time()
    try:
        data = page.evaluate(_EXTRACT_JS)
    except Exception as exc:
        return { "ran": False, "error": str(exc), "issues": [], ... }
    issues = analyze(data)
    return { "ran": True, "issues": issues, "duration_seconds": ..., ... }
```

Why the split:

1. **Tests don't need a browser.** Every rule has a unit test that calls `analyze()` with a fixture dict. Playwright isn't imported. Tests run in 2 seconds.
2. **Failure modes are separated.** Network / browser / CSP issues manifest in `run()`. Logic bugs manifest in `analyze()`. A unit-test failure points at logic; an e2e failure points at the environment.
3. **JS stays small and focused.** The extractor only needs to query the DOM and return JSON-serializable data. No rule logic in JavaScript.
4. **New rules are trivial to add.** Extend the extractor, add a branch in `analyze()`, add a test. No orchestrator changes needed.

The orchestrator's `_module_summaries()` inspects the return dict and records `ran`, `issues_found`, `duration_seconds`, `error`, plus any module-specific metadata the module chose to include.

---

## Deduplication strategy

Current key: `(selector, rule)`. Two issues dedup only when both the element selector AND the rule name match exactly. The one with higher severity wins; on ties, first seen wins.

### What this correctly handles

- Same module firing the same rule twice on the same element (e.g. a bug in the extractor).
- Cross-module collision where two different analyzers *independently* flag the same exact rule name (rare but possible).

### What this deliberately does NOT handle

- **Cross-module overlap with different rule names.** axe's `label` rule and our `forms-input-no-label` are semantically the same problem but their rule names don't match, so both appear in the output. That's noisier than ideal but correct. The fix (whenever we add it) is a targeted mapping of known-equivalent rules, or UI-layer grouping by WCAG criterion.
- **Within-module different-rule overlap.** `forms-input-no-label` and `forms-aria-invalid-no-description` may both fire on the same input. They describe distinct problems and both are kept.

### Earlier (broken) strategy

The first cut keyed by `(selector, rule_root)` where `rule_root = rule.split('-', 1)[0]`. Intent: merge `color-contrast` (axe) with a hypothetical `color-ratio` (ours). Actual effect: merged `forms-input-no-label` with `forms-aria-invalid-no-description` because they share the `forms` prefix — two distinct problems got collapsed into one.

The integration test caught it because the planted fixture had an `<input>` that should have produced both rules and only one survived. That's the value of the e2e test — no amount of unit testing would have surfaced this, because each rule fires correctly in isolation.

---

## Path A vs Path B: the screen-reader module

Two independent code paths with a stable merge point.

### Path A — Chromium a11y tree (shipped)

`page.accessibility.snapshot(interesting_only=False)` returns the AXTree — the same data screen readers consume via UIA / AT-SPI / IAccessible2 before applying their own verbosity layer. We walk the tree and emit four rules: `sr-silent-interactive`, `sr-empty-heading`, `sr-dialog-no-name`, `sr-duplicate-landmark`.

Runs on any platform. No external dependencies. Catches the canonical "silent element" class of problems.

### Path B — real NVDA (deferred)

When it lands, it will be a Windows-only overlay:

1. An NVDA add-on (`nvda_addon/globalPlugins/speechCapture.py`) overrides `speech.speak()` to write every announcement to a capture channel, timestamped.
2. A Python `NVDAController` on the worker starts NVDA if needed, opens the channel, drives Playwright headfully (NVDA only reads the focused window), and synchronizes Tab presses with channel reads.
3. Each `{tab_index, focused_selector, nvda_said}` tuple feeds an analyzer that emits additional rules the a11y tree can't catch: `nvda-silent-element` (tree says named, NVDA said nothing), `nvda-announced-as-clickable` (unsemantic interactive element), `nvda-read-order-mismatch` (browse-mode reading order differs from visual order).

### Merge point

`AuditOrchestrator.run()` always calls `screen_reader.run(page, options)` (Path A). If `skip_nvda` is false AND a Windows worker is available, it then calls `NVDAController.analyze_results(...)` and merges its issues into the same `screen_reader` module bucket. The deduplicator handles overlap.

API shape is stable: clients always see one `modules.screen_reader` entry. A `modules.screen_reader.nvda` sub-field reports on whether Path B ran or was skipped.

### Queue routing (when Path B lands)

Celery doesn't know which worker has NVDA. The plan is a two-queue setup:

- `audit.default` — all workers subscribe.
- `audit.nvda` — only Windows+NVDA workers subscribe.

The API inspects `skip_nvda` in the request and enqueues to the right queue. Jobs requiring NVDA that have no eligible worker will sit queued until one comes online, rather than silently running Path A only.

---

## Why static modules run sequentially

Early versions used a `ThreadPoolExecutor(max_workers=5)` to run the five static-analysis modules in parallel, per the original design doc. That crashes with:

```
greenlet.error: cannot switch to a different thread (which happens to have exited)
```

Playwright's sync API is implemented on top of greenlets. Each `page.evaluate()` call yields to an event loop tied to a specific thread. Calling `page.evaluate` from two threads concurrently breaks the greenlet invariant and throws immediately.

Options considered:

1. **Serialize the static phase.** Each module's extractor takes <100ms; the entire phase takes <1s. Parallelism isn't worth the complexity.
2. **Use async Playwright + asyncio.gather.** Would require the whole codebase to go async. Big rewrite for marginal gain.
3. **One browser context per thread.** Each thread gets its own page. Doubles memory, complicates lifecycle, still doesn't help because the modules are all running against the SAME page snapshot.

Option 1 shipped. The orchestrator comment documents the constraint so nobody re-adds the thread pool.

If profiling ever shows the static phase is a bottleneck, the right fix is option 2 (async throughout). Not before.
