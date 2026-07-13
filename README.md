# Accessibility Audit Server

A FastAPI service that automates checks for common WCAG 2.2 barriers. It combines axe-core with custom analysis modules, optional real-NVDA support via a Windows worker, multi-page audits, form-based login, and structured JSON/HTML reports. Automated results identify likely barriers; they do not establish WCAG conformance without manual review.

One URL in, full audit out. Designed for site-owner tooling — run it against your own sites from CI, as a scheduled job, or ad-hoc from the CLI.

Verified against real sites:

| Target                                            | Score | Issues found |
|---------------------------------------------------|------:|-------------:|
| `example.com` (minimal baseline)                   | 100/A | 0            |
| `w3.org/WAI/demos/bad/before/home.html` (known-bad) | 0/F   | 111          |
| `bbc.com` (production site)                        | 0/F   | 34           |

- **API reference:** [docs/api.md](docs/api.md)
- **Rule catalog:** [docs/rules.md](docs/rules.md)
- **Configuration reference:** [docs/configuration.md](docs/configuration.md)
- **Architecture notes:** [docs/architecture.md](docs/architecture.md)
- **Repository audit / improvement backlog:** [docs/repository_audit.md](docs/repository_audit.md)
- **Windows NVDA worker setup:** [docs/windows_worker.md](docs/windows_worker.md)

---

## Table of contents

- [What it does](#what-it-does)
- [Quick start — local](#quick-start--local)
- [Quick start — Docker Compose](#quick-start--docker-compose)
- [Deploy on Coolify / any PaaS](#deploy-on-coolify--any-paas)
- [NVDA (Path B) on a Windows laptop](#nvda-path-b-on-a-windows-laptop)
- [API overview](#api-overview)
- [Multi-page audits](#multi-page-audits)
- [Authenticated audits (form login)](#authenticated-audits-form-login)
- [Configuration](#configuration)
- [Testing](#testing)
- [What's covered — and what isn't](#whats-covered--and-what-isnt)
- [Repository layout](#repository-layout)

---

## What it does

Per audit, the pipeline runs:

1. **Playwright** launches headless Chromium, applies cookies / HTTP auth / form login if configured.
2. **10 analysis modules** run against the loaded page:
   - `wcag_engine` — axe-core (industry standard rule engine)
   - `structure` — landmarks, headings, language
   - `aria` — roles, labelling, hidden-focusable
   - `media` — image/video alt text and captions
   - `cognitive` — link text quality, duplicate labels
   - `visual` — motion, tiny text, contrast gaps
   - `responsive` — viewport, target size
   - `keyboard` — tab order, focus indicators
   - `forms` — labels, autocomplete, error handling
   - `preferences` — `prefers-reduced-motion` and `forced-colors` (Windows High Contrast) support
   - `screen_reader` (Path A) — Chromium a11y-tree analysis
   - `screen_reader` (Path B) — real NVDA speech capture (Windows worker)
3. **Deduplicator** collapses overlapping findings (axe + a custom module flagging the same element).
4. **Scorer** computes a 0-100 score, an A-F grade, and per-WCAG-principle breakdowns.
5. **Result** is saved to SQLite, cached in Redis, and available as JSON or a rendered HTML report.

---

## Quick start — local

### Prerequisites

- Python 3.10+ (CI currently runs Python 3.11)
- Redis (only required for the background queue; the quick endpoint works without it)

### Install

```bash
git clone https://github.com/WOLFIEEEE/autoaudit.git
cd autoaudit

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Install the Chromium binary Playwright drives:
python -m playwright install chromium

# Vendor axe-core so the audit can run offline:
python scripts/fetch_axe.py
```

### Run the server

```bash
# In one terminal:
python main.py
#  -> http://localhost:8000
#  -> OpenAPI docs at http://localhost:8000/docs

# In a second terminal (if you want the full /audit endpoint):
redis-server &   # or: docker run -p 6379:6379 redis:7-alpine
python scripts/run_worker.py
```

Without Redis+worker, you can still use `/audit/quick` (axe-only, synchronous) and `/health`.

### Try it

```bash
# Quick audit (axe-core only, synchronous — works without worker):
curl -X POST http://localhost:8000/audit/quick \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.w3.org/WAI/demos/bad/before/home.html"}'

# Full audit (all modules, async):
curl -X POST http://localhost:8000/audit \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
#  -> {"job_id": "...", "status": "queued", "poll_url": "/audit/..."}

# Poll:
curl http://localhost:8000/audit/<job_id>

# Human-readable report:
open http://localhost:8000/audit/<job_id>/html
```

---

## Quick start — Docker Compose

One command brings up the whole stack (server + worker + Redis):

```bash
docker compose up --build
```

Services:
- `server` — FastAPI on `:8000`
- `worker` — Celery Linux worker (default queue)
- `redis`  — broker + cache

Data persists in `./data/audits.db`. The server container has a HEALTHCHECK against `/health`, so `docker compose ps` will show health state.

## Deploy on Coolify / any PaaS

The project is Coolify-ready out of the box:

1. Point Coolify at the GitHub repo. It auto-detects `docker-compose.yml`.
2. Expose port `8000` on the `server` service.
3. (Optional) Add an `API_KEYS` env var to require Bearer-token auth on every endpoint.
4. (Optional) Add `RATE_LIMIT_PER_MIN=60` to enable per-IP / per-key rate limiting.
5. (Optional, recommended) Set `LOG_FORMAT=json` so logs are machine-parseable.

See [docs/configuration.md](docs/configuration.md) for the full env var reference.

**Important caveat:** Coolify runs on Linux. If you want real-NVDA auditing (Path B), the Windows worker lives *outside* Coolify and connects back to the same Redis. See below.

---

## NVDA (Path B) on a Windows laptop

Full setup guide: **[docs/windows_worker.md](docs/windows_worker.md)**. Summary:

### The split

```
┌─────────────────────────────┐       Celery broker          ┌──────────────────────────┐
│   Linux server + worker     │  ◄──────(Redis)──────►       │   Windows laptop/VM       │
│   Coolify / Docker / VM     │                              │                          │
│   queue=default             │                              │   queue=nvda             │
│   Path A + all automated    │                              │   Path B (real NVDA)     │
└─────────────────────────────┘                              └──────────────────────────┘
```

The Linux worker runs the full audit and saves it with `nvda_status=pending`. If a Windows worker is online, it picks up `audit.run_nvda` from the `nvda` queue, runs NVDA against the same URL, and merges findings into the same DB row. Status progresses: `pending → completed`.

### Quick setup on the laptop

```powershell
# Windows PowerShell, from the repo root
git clone https://github.com/WOLFIEEEE/autoaudit.git
cd autoaudit
python -m pip install -r requirements.txt
python -m playwright install chromium

# Install NVDA: https://www.nvaccess.org/download/

# Connect to Redis (Tailscale recommended — see docs for alternatives):
$env:REDIS_URL = "redis://:your-password@100.x.y.z:6379/0"

# Start the NVDA worker (one-click):
.\scripts\run_worker_windows.ps1
```

For "always-on" operation (survives reboots, runs before login), install as a Windows service with NSSM — steps in [docs/windows_worker.md](docs/windows_worker.md#install-as-a-windows-service-always-on).

### Verify

```bash
# From any client:
curl -X POST https://your-server/audit \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "options": {"skip_nvda": false}}'

# Poll — nvda_status should go pending → completed:
curl https://your-server/audit/<job-id> | jq .nvda_status
```

If `nvda_status` stays `pending`, the Windows worker isn't reaching Redis — see the [troubleshooting table](docs/windows_worker.md#troubleshooting).

---

## API overview

| Method | Path                      | Description |
|--------|---------------------------|-------------|
| POST   | `/audit`                  | Queue a full audit (single URL or up to 25 URLs) |
| GET    | `/audit/{job_id}`         | Fetch the result as JSON |
| GET    | `/audit/{job_id}/html`    | Rendered HTML report |
| DELETE | `/audit/{job_id}`         | Remove an audit |
| POST   | `/audit/quick`            | Synchronous axe-only scan (no queue required) |
| GET    | `/health`                 | Liveness + dependency probe (DB / Redis) |

Full OpenAPI schema at `/docs` when running. Structured reference: [docs/api.md](docs/api.md).

---

## Multi-page audits

Submit a list of URLs and the orchestrator audits each page in a single browser context (cookies / login persist across pages):

```bash
curl -X POST http://localhost:8000/audit \
  -H "Content-Type: application/json" \
  -d '{
    "urls": [
      "https://example.com/",
      "https://example.com/about",
      "https://example.com/pricing"
    ]
  }'
```

The result includes a `pages` array with per-page summaries plus an aggregate top-level `summary` across all pages. Max 25 URLs per request.

---

## Authenticated audits (form login)

Pass a `login` config in `options` — the browser navigates to the login page, fills and submits the form, waits for the success marker, then audits the target URL(s):

```json
{
  "url": "https://app.example.com/dashboard",
  "options": {
    "login": {
      "url": "https://app.example.com/login",
      "username_selector": "#email",
      "password_selector": "#password",
      "submit_selector": "button[type=submit]",
      "username": "audit-user@example.com",
      "password": "…",
      "success_selector": ".user-menu",
      "timeout_seconds": 15
    }
  }
}
```

Combine with `urls` to audit a whole authenticated flow. Cookies persist across pages in the same audit.

---

## Configuration

All configuration is environment-variable driven. Highlights:

| Variable                | Default                          | Purpose |
|-------------------------|----------------------------------|---------|
| `REDIS_URL`             | `redis://localhost:6379/0`       | Celery broker + cache |
| `DATABASE_URL`          | `sqlite:///./data/audits.db`     | Audit result persistence (SQLite) |
| `MAX_AUDIT_SECONDS`     | `180`                            | Celery soft-timeout per audit |
| `CACHE_TTL_SECONDS`     | `900`                            | Same-URL result cache TTL |
| `CACHE_ENABLED`         | `true`                           | Disable the optional Redis result cache |
| `REDIS_REQUIRED`        | `false`                          | Treat Redis failure as unhealthy (Compose sets true) |
| `API_KEYS`              | (unset → auth off)               | Comma-separated allowed keys |
| `RATE_LIMIT_PER_MIN`    | `0` (off)                        | Per-key / per-IP rate limit |
| `LOG_FORMAT`            | `text`                           | Set to `json` for structured logs |
| `LOG_LEVEL`             | `INFO`                           |         |
| `ALLOW_PRIVATE_TARGETS` | `false`                          | Disable SSRF check for local dev |
| `SKIP_NVDA`             | auto (true everywhere but Windows) | Force-skip Path B |
| `CELERY_POOL`           | `prefork` on Linux, `solo` on Windows | Worker pool type |
| `CELERY_CONCURRENCY`    | `2`                              | Worker processes |
| `CELERY_QUEUES`         | `default`                        | Which queues the worker consumes. Set to `nvda` on Windows. |

Full reference in [docs/configuration.md](docs/configuration.md).

### Security defaults

- **SSRF protection:** `/audit` and `/audit/quick` reject URLs that resolve to private / loopback / link-local / reserved addresses. Override with `ALLOW_PRIVATE_TARGETS=1` for local dev.
- **Input bounds:** `timeout_seconds`, `viewport` size, header count, cookie count, URL length — all capped. Audit payloads capped at 16 MiB.
- **API-key auth:** set `API_KEYS` and requests need `X-API-Key: …` or `Authorization: Bearer …`.
- **Rate limiting:** set `RATE_LIMIT_PER_MIN=60` and the middleware enforces it with LRU eviction (bounded memory).
- **Exception hygiene:** 500 responses return a request ID, never the stack trace.

---

## Testing

```bash
# Unit + API tests (about 1-2 minutes on a typical developer machine):
python -m pytest -m "not slow"

# Everything including Playwright integration (needs Chromium + vendor/axe):
python -m pytest
```

The repository currently contains more than 400 tests. Browser-backed tests are
marked `slow`; CI runs both the fast and browser-backed jobs on every push.

---

## What's covered — and what isn't

Automated accessibility tooling catches roughly **30-40% of WCAG barriers**. This server is at the high end of that range, but fundamental limits apply.

### Covered
- WCAG 2.2 A/AA automated rules (axe-core + 9 custom modules)
- Windows / NVDA screen reader (Path B on Windows worker)
- Chromium a11y-tree analysis (Path A, cross-platform)
- `prefers-reduced-motion` and `forced-colors` support detection
- Multi-page flows with shared cookies / login
- HTTP basic auth and form-based login
- Color contrast (via axe-core), target size, keyboard order, ARIA usage

### Not covered (fundamental limits, not bugs)
- **JAWS** — ~40% of screen reader users, no headless automation exists
- **VoiceOver (macOS/iOS)** and **TalkBack (Android)** — native mobile a11y
- **Human judgment** — is alt text actually *useful*? Is the heading outline logical? Are error messages understandable to someone with a cognitive disability?
- **PDF / document accessibility**
- **Video caption quality** (presence detectable, accuracy not)
- **Real user testing** — the only way to find the barriers tooling misses

The HTML report surfaces this disclosure so non-technical stakeholders see it, not just API consumers.

---

## Repository layout

```
autoaudit/
├── audit/                       # Analysis modules (10 modules + browser driver)
│   ├── browser.py               # Playwright lifecycle, login, retry
│   ├── orchestrator.py          # Runs modules, aggregates results, supports multi-URL
│   ├── wcag_engine.py           # axe-core injection
│   ├── {structure,aria,media,cognitive,visual,responsive,keyboard,forms}.py
│   ├── preferences.py           # prefers-reduced-motion / forced-colors
│   ├── screen_reader.py         # Path A (a11y tree) + Path B (NVDA) stubs
│   ├── deduplicator.py
│   └── scorer.py
├── server/                      # FastAPI app
│   ├── app.py                   # Routes + /health with dep probes
│   ├── models.py                # Pydantic schemas, SSRF guard, input bounds
│   ├── database.py              # SQLite with WAL mode + JSON size caps
│   ├── cache.py                 # Redis cache (optional)
│   ├── middleware.py            # Request ID, API key, rate limit, structured logs
│   ├── tasks.py                 # Celery tasks (run + run_nvda)
│   └── config.py
├── templates/report.html.j2     # Rendered HTML audit report
├── docs/                        # API, rules, configuration, architecture, Windows worker
├── scripts/
│   ├── run_worker.py            # Celery worker launcher (queues via env)
│   ├── run_worker_windows.ps1   # One-click launcher for the NVDA laptop
│   ├── fetch_axe.py
│   └── cleanup_audits.py
├── tests/                       # 166 unit/API tests + 4 integration
├── Dockerfile                   # With HEALTHCHECK
├── docker-compose.yml           # server + worker + redis
├── celery_app.py                # Two-queue routing: default + nvda
├── main.py
└── requirements.txt
```

---

## License

Add your license here.
