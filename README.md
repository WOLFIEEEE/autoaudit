# Accessibility Audit Server

A FastAPI server that takes a URL and returns a structured WCAG 2.2 audit. One endpoint in, full audit out. Designed for site-owner tooling — accessibility testing on your own sites, integrated into CI or run ad-hoc.

Every audit module in the plan ships with real analysis, verified by an end-to-end Playwright integration test that runs the full pipeline (browser + axe-core injection + 9 modules + scorer + deduplicator) against a fixture page with planted issues. The only deferred piece is **Path B** of the screen-reader module — real NVDA speech capture, which needs a Windows host.

- **API reference:** [docs/api.md](docs/api.md)
- **Rule catalog:** [docs/rules.md](docs/rules.md)
- **Configuration reference:** [docs/configuration.md](docs/configuration.md)
- **Architecture notes:** [docs/architecture.md](docs/architecture.md)

---

## Table of contents

- [Status](#status)
- [Quick start — local](#quick-start--local)
- [Quick start — Docker](#quick-start--docker)
- [API overview](#api-overview)
- [Rule catalog overview](#rule-catalog-overview)
- [robots.txt policy](#robotstxt-policy)
- [Repository layout](#repository-layout)
- [Running tests](#running-tests)
- [Extending the server](#extending-the-server)
- [What differentiates this from axe-core / Lighthouse / Pa11y / WAVE](#what-differentiates-this-from-axe-core--lighthouse--pa11y--wave)

---

## Status

| Component         | State          | Notes |
|-------------------|----------------|-------|
| FastAPI API       | Working        | 5 endpoints, full OpenAPI docs at `/docs` when running |
| Celery + Redis    | Working        | Background job queue with graceful degradation if Redis absent |
| SQLite store      | Working        | Results persisted per `job_id` |
| Redis cache       | Optional       | Same-URL results cached for `CACHE_TTL_SECONDS` (default 900s) |
| Playwright        | Working        | Chromium, headless by default |
| axe-core          | Working        | Injected from local vendor file or CDN |
| Scoring + grading | Working        | Overall + per-WCAG-principle scores, A–F grade |
| Deduplication     | Working        | `(selector, rule)` keying; higher severity wins |
| structure         | Working        | 6 rules |
| aria              | Working        | 4 rules |
| media             | Working        | 5 rules |
| cognitive         | Working        | 3 rules |
| visual            | Working        | 3 rules (static; contrast handled by axe) |
| keyboard          | Working        | 5 rules (real tab walk) |
| forms             | Working        | 4 rules |
| responsive        | Working        | 3 rules |
| screen_reader     | Path A working | 4 rules via Chromium a11y tree. Path B (real NVDA) deferred |

**Total: 37 custom rules + full axe-core ruleset at the configured WCAG level.**

---

## Quick start — local

### Prerequisites

- Python 3.11+
- Redis (only for the queued `/audit` endpoint; `/audit/quick` is synchronous and needs no Redis)

### Install

```bash
pip install -r requirements.txt
playwright install chromium
python scripts/fetch_axe.py         # vendor axe-core locally (recommended)
```

### Run

```bash
# Terminal 1 — Redis (skip if using only /audit/quick)
docker run --rm -p 6379:6379 redis:7

# Terminal 2 — Celery worker
python scripts/run_worker.py

# Terminal 3 — API server
python main.py
```

The server listens on `http://localhost:8000`. OpenAPI docs are auto-generated at `http://localhost:8000/docs`.

### Smoke test

```bash
curl -s -X POST http://localhost:8000/audit/quick \
  -H 'content-type: application/json' \
  -d '{"url": "https://example.com"}' | jq '.summary, .modules'
```

---

## Quick start — Docker

```bash
docker compose up --build
curl -s http://localhost:8000/health
```

The compose file spins up three containers: `server`, `worker`, and `redis`. SQLite data is persisted in `./data`. NVDA is Windows-only and is not part of the Docker image; see [docs/architecture.md](docs/architecture.md#path-b-real-nvda-worker) for the hybrid deployment that pairs this Docker stack with a Windows worker.

---

## API overview

Five endpoints. Full schemas, examples, and error shapes in [docs/api.md](docs/api.md).

| Method | Path                         | Purpose                                                    |
|--------|------------------------------|------------------------------------------------------------|
| POST   | `/audit`                     | Queue a full audit (returns `job_id`, run asynchronously)  |
| GET    | `/audit/{job_id}`            | Fetch status + results for a queued audit                  |
| GET    | `/audit/{job_id}/html`       | Render a human-readable HTML report                        |
| DELETE | `/audit/{job_id}`            | Delete stored results                                      |
| POST   | `/audit/quick`               | Synchronous axe-core-only scan (< 10 s typical)            |
| GET    | `/health`                    | Liveness + platform/NVDA capability                        |

Minimal request:

```json
{
    "url": "https://example.com"
}
```

Full request with all options:

```json
{
    "url": "https://example.com",
    "options": {
        "level": "aa",
        "modules": ["all"],
        "skip_nvda": true,
        "wait_ms": 400,
        "max_tabs": 500,
        "viewport": { "width": 1280, "height": 720 },
        "cookies": [],
        "headers": {},
        "basic_auth": { "username": "", "password": "" },
        "timeout_seconds": 120
    }
}
```

See [docs/api.md](docs/api.md) for the complete response shape, including the summary block (score, grade, per-principle breakdown), the flat `issues` array (every issue, sorted by severity), and the `modules` map (per-module execution metadata).

---

## Rule catalog overview

37 custom rules plus the full axe-core ruleset at the configured WCAG level. Full descriptions, WCAG mappings, severities, and fix suggestions in [docs/rules.md](docs/rules.md).

| Module         | Rules |
|----------------|-------|
| wcag_engine    | All axe-core rules matching the configured level tags (`wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`, …) |
| structure      | `structure-html-lang`, `structure-title-missing`, `structure-no-h1`, `structure-multiple-h1`, `structure-heading-skip`, `structure-no-main`, `structure-table-no-th` |
| aria           | `aria-invalid-role`, `aria-labelledby-missing`, `aria-describedby-missing`, `aria-hidden-focusable` |
| media          | `media-img-no-alt`, `media-img-placeholder-alt`, `media-img-decorative-text`, `media-video-no-track`, `media-autoplay` |
| cognitive      | `cognitive-empty-link`, `cognitive-generic-link-text`, `cognitive-duplicate-link-text` |
| visual         | `visual-marquee-or-blink`, `visual-infinite-animation`, `visual-tiny-text` |
| keyboard       | `keyboard-trap-suspected`, `keyboard-no-focus-indicator`, `keyboard-no-accessible-name`, `keyboard-positive-tabindex`, `keyboard-generic-focusable` |
| forms          | `forms-input-no-label`, `forms-radio-group-no-fieldset`, `forms-aria-invalid-no-description`, `forms-missing-autocomplete` |
| responsive     | `responsive-viewport-meta-missing`, `responsive-viewport-zoom-disabled`, `responsive-target-size` |
| screen_reader  | `sr-silent-interactive`, `sr-empty-heading`, `sr-dialog-no-name`, `sr-duplicate-landmark` |

---

## robots.txt policy

**This tool ignores robots.txt by design.** Accessibility auditing is performed on behalf of website owners who have the right to test their own sites. robots.txt is a directive for search-engine crawlers, not for site-owner tooling — Lighthouse, Pa11y, axe-cli, and similar tools all behave the same way.

This tool:

- Does not import `urllib.robotparser`
- Does not fetch or parse robots.txt files
- Uses an honest, identifiable `User-Agent` string
- Does not crawl beyond the single URL provided
- Makes no attempt to index content
- Does **not** use anti-bot-detection flags such as `--disable-blink-features=AutomationControlled`

If you are auditing a site you do not own, ensure you have permission from the site owner before running this tool.

There is intentionally **no `ignore_robots_txt` option** on the request schema. The behavior is not configurable, so exposing the field would misrepresent the API.

---

## Repository layout

```
server/                 FastAPI app, Pydantic models, Celery tasks, storage, cache
    app.py              Route handlers
    models.py           Request/response schemas
    tasks.py            Celery task: run_audit_task
    database.py         SQLite job storage
    cache.py            Redis cache (optional, degrades gracefully)
    config.py           Env-var-driven configuration
audit/                  Audit pipeline
    orchestrator.py     Runs every module, aggregates, scores, dedups
    browser.py          Playwright lifecycle
    wcag_engine.py      axe-core injection + result normalization
    scorer.py           Score + grade + per-principle breakdown
    deduplicator.py     (selector, rule) keyed duplicate merger
    _issue.py           Shared issue-dict helper
    structure.py        \
    aria.py              \
    media.py              |
    cognitive.py          | Per-module analyzers. Each exposes
    visual.py             | run(page, options) -> dict plus a
    keyboard.py           | pure-Python analyze() that tests call
    forms.py              | with fixture data.
    responsive.py        /
    screen_reader.py    /  Path A a11y-tree analyzer + Path B NVDA placeholder
celery_app.py           Celery factory
main.py                 Uvicorn entry point
scripts/
    fetch_axe.py        Vendor axe-core into ./vendor/
    run_worker.py       Start a Celery worker
docs/
    api.md              Endpoint-by-endpoint API reference
    rules.md            Every rule, WCAG mapping, severity, fix suggestion
    configuration.md    Environment variables, options, deployment knobs
    architecture.md     Component diagram, Path A/B split, queue routing
tests/
    test_*.py           Fast unit tests for analyze() functions
    test_api.py         TestClient-based API endpoint tests
    integration/
        test_e2e.py     End-to-end Playwright + axe + orchestrator
    fixtures/
        issues_sample.html
        good_page.html
        bad_contrast.html
vendor/
    axe.min.js          Fetched by scripts/fetch_axe.py
Dockerfile
docker-compose.yml
pytest.ini
requirements.txt
```

---

## Running tests

```bash
# Fast unit tests (no Playwright, no Redis, no network) — ~2 s
pytest -m "not slow"

# Full suite, including the end-to-end Playwright integration test — ~55 s
pytest
```

The fast suite covers scoring, deduplication, tag parsing, API request validation, and every module's pure-Python `analyze()` function with fixture data. No external dependencies.

The `slow` suite adds `tests/integration/test_e2e.py`, which:

- Starts a local HTTP server bound to `tests/fixtures/`
- Loads a fixture HTML page containing deliberate accessibility failures across every module
- Runs both `/audit/quick` (axe-core only) and the full orchestrator
- Asserts that representative rules from each module fire, every module reports `ran: true`, and the deduplicator produces no duplicates

Requires:

- `playwright install chromium` (one-time)
- `python scripts/fetch_axe.py` (vendors axe-core locally — the test will not reach the CDN in sandboxed CI environments)

If either dependency is missing, the integration test auto-skips with an actionable message rather than failing.

---

## Extending the server

Every custom module follows the same shape:

```python
# audit/<name>.py

_EXTRACT_JS = """() => { /* DOM query that returns a dict */ }"""

def analyze(data: dict) -> list[dict]:
    """Pure-Python rule logic. Takes extracted DOM data, returns issues.
    Tests call this directly with fixture dicts."""
    ...

def run(page, options=None) -> dict:
    """Thin wrapper: page.evaluate the extractor, call analyze(),
    return the module-result dict the orchestrator expects."""
    ...
```

**To add a new rule to an existing module:**

1. Extend `_EXTRACT_JS` to capture the new data you need from the DOM.
2. Add a new branch in `analyze()` using the `make_issue(...)` helper in `audit/_issue.py`.
3. Add a unit test in `tests/test_<module>.py` — call `analyze()` with a fixture dict.
4. Add a matching `data-issue` marker to `tests/fixtures/issues_sample.html` and extend the e2e assertions.

**To add a new module:**

1. Create `audit/<name>.py` with the pattern above.
2. Register it in `audit/orchestrator.py`. Static-DOM modules go in `STATIC_MODULES`; interactive modules (those that mutate focus or viewport) are called explicitly in `AuditOrchestrator.run()`.
3. Write unit tests and fixtures as above.

The orchestrator aggregates issues automatically, the scorer derives penalties from severity, and the deduplicator merges same-element same-rule duplicates. No changes needed anywhere else.

See [docs/architecture.md](docs/architecture.md) for the per-phase execution model and the rationale for running static modules sequentially (Playwright's sync API is greenlet-based and unsafe across threads).

---

## What differentiates this from axe-core / Lighthouse / Pa11y / WAVE

| Feature                          | axe | Lighthouse | Pa11y | WAVE | **This tool** |
|----------------------------------|-----|------------|-------|------|---------------|
| WCAG automated rules             | ✓   | ✓          | ✓     | ✓    | ✓ (axe-core)  |
| Full tab order mapping           | —   | —          | —     | —    | ✓             |
| Keyboard trap detection          | —   | —          | —     | —    | ✓             |
| Focus-indicator visibility       | —   | —          | —     | —    | ✓             |
| Positive-tabindex anti-pattern   | —   | —          | —     | —    | ✓             |
| `<div tabindex>` detection       | —   | —          | —     | —    | ✓             |
| Placeholder-alt detection        | —   | —          | —     | —    | ✓             |
| Decorative image with text alt   | —   | —          | —     | —    | ✓             |
| Target size ≥ 24×24 (WCAG 2.2)   | —   | ✓          | —     | —    | ✓             |
| Viewport-zoom disabled           | —   | ✓          | —     | —    | ✓             |
| Duplicate-landmark detection     | —   | —          | —     | —    | ✓             |
| a11y-tree silent-element         | —   | —          | —     | —    | ✓             |
| Generic link-text detection      | —   | —          | —     | —    | ✓             |
| Duplicate link text → different URLs | — | —       | —     | —    | ✓             |
| aria-invalid without description | —   | —          | —     | —    | ✓             |
| Missing autocomplete (1.3.5)     | —   | —          | —     | —    | ✓             |
| Single API endpoint              | —   | —          | —     | —    | ✓             |
| Scored report with A–F grade     | —   | ✓          | —     | —    | ✓             |
| Real NVDA announcement capture   | —   | —          | —     | —    | Path B deferred (Windows worker) |
| robots.txt explicitly ignored    | N/A | N/A        | N/A   | N/A  | ✓ (documented) |

---

## License and contributing

Development happens on the `claude/a11y-audit-server-QVeA0` branch. Contributions via PR.

For issues found by a user running this tool against their own site, include the output of `GET /audit/{job_id}` when filing a report so module states and issue dedup keys are visible.
