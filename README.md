# Accessibility Audit Server

FastAPI server that accepts a URL and returns a structured WCAG 2.2 audit.
One endpoint in, full audit out.

This repository is the **Sprint 1–3 MVP** of the full plan: the server,
job queue, browser automation, and axe-core WCAG engine are wired up
end-to-end. The nine additional audit modules (structure, ARIA, media,
cognitive, visual, keyboard, forms, responsive, screen_reader/NVDA) are
scaffolded as stubs returning a valid empty module result, ready to be
filled in module-by-module.

## Status

| Module        | State          |
|---------------|----------------|
| FastAPI API   | Working        |
| Celery + Redis| Working        |
| SQLite store  | Working        |
| Playwright    | Working        |
| axe-core      | Working        |
| Scoring       | Working        |
| Deduplication | Working        |
| structure     | Working (6 rules) |
| aria          | Working (4 rules) |
| media         | Working (5 rules) |
| cognitive     | Working (3 rules) |
| visual        | Stub           |
| keyboard      | Stub           |
| forms         | Stub           |
| responsive    | Stub           |
| screen_reader | Stub (Windows-only when built)|

## Quick start (local)

```bash
pip install -r requirements.txt
playwright install chromium
python scripts/fetch_axe.py        # vendor axe-core locally (optional)

# Terminal 1: Redis (or `docker run -d -p 6379:6379 redis:7`)
# Terminal 2:
python scripts/run_worker.py
# Terminal 3:
python main.py
```

Smoke test:

```bash
curl -s -X POST http://localhost:8000/audit/quick \
  -H 'content-type: application/json' \
  -d '{"url": "https://example.com"}' | jq .
```

## Quick start (Docker)

```bash
docker compose up --build
curl -s http://localhost:8000/health
```

NVDA auditing is Windows-only and is not part of the Docker image.

## API

```
POST   /audit              → queue a full audit (returns job_id)
GET    /audit/{job_id}     → fetch status + results
DELETE /audit/{job_id}     → delete stored results
POST   /audit/quick        → synchronous axe-core-only scan
GET    /health             → server health
```

Request body:

```json
{
  "url": "https://example.com",
  "options": {
    "level": "aa",
    "skip_nvda": true,
    "viewport": { "width": 1280, "height": 720 },
    "timeout_seconds": 120
  }
}
```

## robots.txt policy

**This tool ignores robots.txt by design.** Accessibility auditing is
performed on behalf of website owners who have the right to test their
own sites. robots.txt is a directive for search-engine crawlers, not for
site-owner tooling — Lighthouse, Pa11y, axe-cli, and similar tools all
behave the same way.

This tool:

- Does not import `urllib.robotparser`
- Does not fetch or parse robots.txt files
- Uses an honest, identifiable `User-Agent` string
- Does not crawl beyond the single URL provided
- Makes no attempt to index content
- Does **not** use anti-bot-detection flags such as
  `--disable-blink-features=AutomationControlled`

If you are auditing a site you do not own, ensure you have permission
from the site owner before running this tool.

There is intentionally **no `ignore_robots_txt` option** on the request
schema: the behavior is not configurable, so exposing the field would
misrepresent the API.

## Repository layout

```
server/         FastAPI app, models, tasks, storage, cache
audit/          Orchestrator + per-module audit logic
  browser.py       Playwright lifecycle
  wcag_engine.py   axe-core integration (real)
  orchestrator.py  Runs all modules, aggregates results
  scorer.py        Score + grade calculation
  deduplicator.py  Cross-module issue merging
  {structure,aria,media,cognitive,visual,keyboard,
   forms,responsive,screen_reader}.py  Stubs
tests/          Unit + API tests
scripts/        fetch_axe.py, run_worker.py
config/         YAML defaults
```

## Running tests

```bash
pytest
```

The default suite does not require Playwright or Redis. It covers scoring,
deduplication, tag parsing, and API request validation. Browser-integration
tests will be added behind a `--slow` marker alongside the module work.

## Next sprints

Follow the sprint plan in the project design doc. Each subsequent sprint
drops a real implementation into one of the stub modules behind its
`run(page, options)` interface — no orchestrator changes required.
