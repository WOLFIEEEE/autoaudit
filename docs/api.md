# API reference

Every endpoint, every field, every status code. All request/response bodies are JSON. The server runs on port 8000 by default.

When the server is running locally, interactive OpenAPI docs are at `http://localhost:8000/docs` (Swagger UI) and `http://localhost:8000/redoc` (ReDoc). This document is the authoritative written reference.

- [Authentication](#authentication)
- [Endpoints](#endpoints)
  - [POST /audit — queue a full audit](#post-audit--queue-a-full-audit)
  - [GET /audit/{job_id} — fetch results](#get-auditjob_id--fetch-results)
  - [GET /audit/{job_id}/html — HTML report](#get-auditjob_idhtml--html-report)
  - [DELETE /audit/{job_id} — delete results](#delete-auditjob_id--delete-results)
  - [POST /audit/quick — synchronous axe-only scan](#post-auditquick--synchronous-axe-only-scan)
  - [GET /health — liveness and capability](#get-health--liveness-and-capability)
- [Schemas](#schemas)
  - [AuditRequest](#auditrequest)
  - [AuditOptions](#auditoptions)
  - [Issue](#issue)
  - [Summary](#summary)
  - [ModuleSummary](#modulesummary)
  - [AuditResponse](#auditresponse)
- [Status codes and error shapes](#status-codes-and-error-shapes)
- [Job lifecycle](#job-lifecycle)

---

## Authentication

Disabled by default — the server is intended for trusted networks. Set the
`API_KEYS` environment variable to a comma-separated list of keys to turn it
on:

```bash
export API_KEYS="dev-key-abc,prod-key-xyz"
```

When enabled, every endpoint **except** `/health`, `/docs`, `/redoc`, and
`/openapi.json` requires either an `X-API-Key` header or a `Authorization:
Bearer <key>` header. A missing or invalid key returns `401 {"detail":
"Invalid or missing API key"}`.

## Rate limiting

Off by default. Set `RATE_LIMIT_PER_MIN=60` (or any positive integer) to
enable a per-key sliding-window limit. When a caller exceeds the budget
the server returns `429 {"detail": "Rate limit exceeded"}` with a
`Retry-After` header in seconds.

The limiter keys by API key ID when auth is enabled, and by client IP
otherwise. `/health` and the docs endpoints are always exempt.

## Observability

Every request gets an `X-Request-ID` header on the response. Callers may
send their own via the same header to correlate with upstream traces;
otherwise the server generates a fresh UUID. Log records emit the
request ID in brackets:

```
2026-04-15T17:13:50 INFO [f54040e205ac] a11y_audit: GET /audit/xyz/html -> 200 (23 ms)
```

Set `LOG_FORMAT=json` for line-per-record JSON output instead.

---

## Endpoints

### POST /audit — queue a full audit

Queues a full audit job. Returns immediately with a `job_id`; the actual audit runs asynchronously on a Celery worker and typically completes in 30–90 seconds depending on page complexity.

If a recent result for the same URL is cached (see `CACHE_TTL_SECONDS` in [configuration.md](configuration.md)), the cached result is returned with `status: "completed"` and no new job is queued.

**Request body:** [AuditRequest](#auditrequest)

**Response:**

```json
{
    "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "queued",
    "estimated_seconds": 60,
    "poll_url": "/audit/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

| Field               | Type    | Notes |
|---------------------|---------|-------|
| `job_id`            | UUID    | Use to poll the result |
| `status`            | string  | `"queued"` for a new job; `"completed"` if a cache hit |
| `estimated_seconds` | int \| null | Best-effort estimate; null when cached |
| `poll_url`          | string  | Convenience path for the GET endpoint |

**Status codes:**

- `200 OK` — job queued (or cache hit)
- `422 Unprocessable Entity` — malformed request body (e.g. missing `url` or unsupported scheme)
- `503 Service Unavailable` — Celery/Redis unreachable; the job could not be enqueued

**Example:**

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'content-type: application/json' \
  -d '{"url": "https://example.com", "options": {"level": "aa"}}'
```

---

### GET /audit/{job_id} — fetch results

Returns the current state of a queued audit. Poll until `status == "completed"` or `status == "failed"`.

**Response:** [AuditResponse](#auditresponse) when completed, or a simplified object for other states:

```json
{
    "job_id": "a1b2c3d4-…",
    "url": "https://example.com",
    "status": "running",
    "timestamp": "2026-04-15T14:30:00Z",
    "duration_seconds": 0.0,
    "summary": { "score": 0, "grade": "?", "total_issues": 0, "by_severity": {...}, "by_principle": {} },
    "issues": [],
    "modules": {},
    "error": null
}
```

Possible `status` values:

| Status        | Meaning |
|---------------|---------|
| `queued`      | Waiting for a worker to pick it up |
| `running`     | Worker is actively auditing |
| `completed`   | Done; `issues`, `summary`, and `modules` are populated |
| `failed`      | Worker raised an exception; see `error` field |

**Status codes:**

- `200 OK` — found (any status)
- `404 Not Found` — no such `job_id`

**Example:**

```bash
curl -s http://localhost:8000/audit/a1b2c3d4-e5f6-7890-abcd-ef1234567890 | jq '.status, .summary'
```

---

### GET /audit/{job_id}/html — HTML report

Returns a standalone, human-readable HTML report for the audit. Useful for
sharing with designers or stakeholders who don't want to read JSON.

The template ships as `templates/report.html.j2` — Jinja2, autoescape on
(all element snippets are safe to render). Dark-mode aware via
`prefers-color-scheme`. Single file, no external assets.

**Status codes:**

- `200 OK` — report rendered
- `404 Not Found` — no such `job_id`

**Example:**

```bash
curl -s http://localhost:8000/audit/a1b2c3d4-e5f6/html > report.html
open report.html
```

The page contains: score + grade, per-severity counts, per-WCAG-principle
breakdown, per-module status table, and every issue (each collapsible,
first one open) with selector, element snippet, WCAG criterion, fix
suggestion, and expandable raw details.

---

### DELETE /audit/{job_id} — delete results

Removes the audit result row from the SQLite store. Useful for cleanup after processing. Does not cancel a running job.

**Response:**

```json
{ "deleted": "a1b2c3d4-e5f6-7890-abcd-ef1234567890" }
```

**Status codes:**

- `200 OK` — deleted
- `404 Not Found` — no such `job_id`

---

### POST /audit/quick — synchronous axe-only scan

Runs axe-core against the URL and returns the result inline. No queue, no polling, no Celery/Redis needed. Typically completes in under 10 seconds.

Useful for CI smoke checks where a full audit would take too long or Celery isn't available.

**Request body:** [AuditRequest](#auditrequest). `options.modules` is ignored; only the WCAG engine runs.

**Response:** [AuditResponse](#auditresponse) with:

- `mode: "quick"` included at the top level
- Only the `wcag_engine` entry populated in `modules`
- `issues` list contains only axe-core violations (normalized into the standard [Issue](#issue) shape)

**Status codes:**

- `200 OK` — audit completed
- `422 Unprocessable Entity` — malformed request
- `500 Internal Server Error` — browser launch failed, page timeout, or axe injection failed

**Example:**

```bash
curl -s -X POST http://localhost:8000/audit/quick \
  -H 'content-type: application/json' \
  -d '{"url": "https://example.com"}' | jq '.summary'
```

---

### GET /health — liveness and capability

Returns a simple liveness check plus platform metadata so clients can detect whether this instance supports Path B (real NVDA).

**Response:**

```json
{
    "status": "ok",
    "platform": "Linux",
    "nvda_capable": false,
    "skip_nvda_default": true
}
```

| Field               | Type   | Notes |
|---------------------|--------|-------|
| `status`            | string | Always `"ok"` when the process is up |
| `platform`          | string | `platform.system()` — `Linux`, `Darwin`, `Windows` |
| `nvda_capable`      | bool   | `true` only on Windows and when the NVDA add-on is installed (currently always mirrors platform check; Path B not yet implemented) |
| `skip_nvda_default` | bool   | Default value for `options.skip_nvda` on this instance |

**Status codes:**

- `200 OK` — always, if the process is up

---

## Schemas

### AuditRequest

```json
{
    "url": "https://example.com",
    "options": { ... }
}
```

| Field     | Type                           | Default | Notes |
|-----------|--------------------------------|---------|-------|
| `url`     | string                         | (required) | Must start with `http://` or `https://` |
| `options` | [AuditOptions](#auditoptions)  | see below | All fields optional |

---

### AuditOptions

```json
{
    "level": "aa",
    "modules": ["all"],
    "skip_nvda": null,
    "wait_ms": 400,
    "max_tabs": 500,
    "viewport": { "width": 1280, "height": 720 },
    "cookies": [],
    "headers": {},
    "basic_auth": null,
    "timeout_seconds": 120
}
```

| Field             | Type                  | Default       | Notes |
|-------------------|-----------------------|---------------|-------|
| `level`           | `"a" \| "aa" \| "aaa"` | `"aa"`       | Passed to axe-core as `runOnly.values` — selects the WCAG level tag set |
| `modules`         | `string[]`            | `["all"]`     | Reserved; currently all modules always run |
| `skip_nvda`       | `bool \| null`        | `null`        | `null` → use server default. `true` → skip Path B overlay (Path A a11y-tree always runs). `false` → attempt Path B (errors cleanly on non-Windows) |
| `wait_ms`         | int                   | `400`         | Delay after each Tab press in the keyboard module |
| `max_tabs`        | int                   | `500`         | Upper bound on Tab presses during keyboard walk |
| `viewport`        | `{ width, height }`   | 1280×720      | Browser viewport |
| `cookies`         | cookie dict array     | `[]`          | Passed to `BrowserContext.add_cookies`. Useful for authenticated pages |
| `headers`         | `{ [name]: string }`  | `{}`          | Extra HTTP headers on every request (`BrowserContext.set_extra_http_headers`) |
| `basic_auth`      | `{ username, password } \| null` | `null` | HTTP basic auth credentials |
| `timeout_seconds` | int                   | `120`         | Per-page navigation timeout |

**Notably absent:** `ignore_robots_txt`. The server never parses robots.txt (see the [README's robots.txt policy](../README.md#robotstxt-policy)). The option was removed rather than hardcoded-to-true because exposing a non-configurable field on the API would misrepresent the behavior.

---

### Issue

Every issue in the `issues` array follows this shape:

```json
{
    "id": "axe-color-contrast-0",
    "module": "wcag_engine",
    "rule": "color-contrast",
    "severity": "serious",
    "principle": "perceivable",
    "wcag_criteria": ["1.4.3"],
    "title": "Elements must have sufficient color contrast",
    "description": "Fix any of the following: Element has insufficient color contrast of 2.84 (foreground color: #999999, background color: #ffffff, font size: 12.0pt (16px), font weight: normal). Expected contrast ratio of 4.5:1",
    "element": {
        "selector": "#hero-subtitle",
        "html_snippet": "<p class=\"subtitle\" style=\"color: #999\">Welcome to our site</p>",
        "text_content": "Welcome to our site"
    },
    "details": {
        "help_url": "https://dequeuniversity.com/rules/axe/4.9/color-contrast",
        "axe_tags": ["cat.color", "wcag2aa", "wcag143"],
        "impact": "serious"
    },
    "fix_suggestion": "Change text color to at least #767676 for 4.5:1 ratio on white background"
}
```

| Field            | Type              | Notes |
|------------------|-------------------|-------|
| `id`             | string            | Opaque, usually `"<module>-<rule>-<index>"`; stable within a single audit |
| `module`         | string            | Source module — one of `wcag_engine`, `structure`, `aria`, `media`, `cognitive`, `visual`, `keyboard`, `forms`, `responsive`, `screen_reader` |
| `rule`           | string            | Rule name — see [docs/rules.md](rules.md) |
| `severity`       | `critical \| serious \| moderate \| minor` | Used for scoring (critical=-8, serious=-4, moderate=-2, minor=-1) |
| `principle`      | string            | One of `perceivable`, `operable`, `understandable`, `robust` |
| `wcag_criteria`  | `string[]`        | WCAG success-criterion numbers (e.g. `["1.4.3"]`) |
| `title`          | string            | Short human-readable summary |
| `description`    | string            | Longer explanation of the problem |
| `element.selector` | string          | CSS selector for the element. Empty for page-level issues |
| `element.html_snippet` | string      | First 200 chars of `outerHTML` |
| `element.text_content` | string      | Visible text (trimmed, up to ~200 chars) |
| `details`        | object            | Rule-specific metadata. Schema varies per rule |
| `fix_suggestion` | string            | Actionable guidance |

---

### Summary

```json
{
    "score": 62,
    "grade": "C",
    "total_issues": 34,
    "by_severity": {
        "critical": 3,
        "serious": 12,
        "moderate": 14,
        "minor": 5
    },
    "by_principle": {
        "perceivable":     { "score": 55, "issues": 14 },
        "operable":        { "score": 70, "issues": 8 },
        "understandable":  { "score": 68, "issues": 7 },
        "robust":          { "score": 52, "issues": 5 }
    }
}
```

| Field          | Type    | Notes |
|----------------|---------|-------|
| `score`        | 0–100   | Start at 100, deduct per issue by severity, floor at 0 |
| `grade`        | `A \| B \| C \| D \| F` | 90+ = A, 75+ = B, 60+ = C, 40+ = D, else F |
| `total_issues` | int     | Post-deduplication count |
| `by_severity`  | object  | Counts by severity bucket |
| `by_principle` | object  | Per-WCAG-principle score and issue count |

Severity penalties:

| Severity  | Points deducted |
|-----------|-----------------|
| critical  | 8               |
| serious   | 4               |
| moderate  | 2               |
| minor     | 1               |

---

### ModuleSummary

Every module appears in the `modules` map of a completed response:

```json
{
    "ran": true,
    "issues_found": 5,
    "duration_seconds": 0.32,
    "error": null
}
```

| Field              | Type             | Notes |
|--------------------|------------------|-------|
| `ran`              | bool             | `true` if the module executed. `false` if skipped or errored |
| `issues_found`     | int              | Count before deduplication |
| `duration_seconds` | float            | Wall-clock time spent in `module.run()` |
| `error`            | string \| null   | Exception message if `ran` is `false` |

Some modules include module-specific extra fields:

- `wcag_engine`: `rules_checked`, `violations`, `passes`, `incomplete`
- `keyboard`: `tab_stops`, `tab_stops_count`, `traps_found`, `cycled`
- `screen_reader`: `mode` (always `"a11y-tree"` for Path A), `tree_nodes`, `note`, and a nested `nvda` object when Path B runs or is skipped
- `responsive`: `targets_measured`, `viewport`
- `visual`: `marquee_elements`, `infinite_animations`, `tiny_text_elements`

---

### AuditResponse

Full completed response:

```json
{
    "job_id": "a1b2c3d4-…",
    "status": "completed",
    "url": "https://example.com",
    "timestamp": "2026-04-15T14:30:00Z",
    "duration_seconds": 47.2,
    "summary": { /* Summary */ },
    "issues": [ /* Issue[] */ ],
    "modules": {
        "wcag_engine": { /* ModuleSummary */ },
        "structure": { /* ModuleSummary */ },
        "aria": { /* ModuleSummary */ },
        "media": { /* ModuleSummary */ },
        "cognitive": { /* ModuleSummary */ },
        "visual": { /* ModuleSummary */ },
        "keyboard": { /* ModuleSummary */ },
        "forms": { /* ModuleSummary */ },
        "responsive": { /* ModuleSummary */ },
        "screen_reader": { /* ModuleSummary */ }
    }
}
```

Issues are sorted by severity: critical → serious → moderate → minor.

---

## Status codes and error shapes

All error responses follow FastAPI's standard shape:

```json
{ "detail": "Human-readable error message" }
```

For `422 Unprocessable Entity`, `detail` is a Pydantic-generated array:

```json
{
    "detail": [
        {
            "type": "value_error",
            "loc": ["body", "url"],
            "msg": "Value error, URL must start with http:// or https://",
            "input": "ftp://example.com"
        }
    ]
}
```

---

## Job lifecycle

```
client               API                    Celery / worker           SQLite         Redis cache
  │                   │                            │                     │                │
  │ POST /audit ─────▶│                            │                     │                │
  │                   │ check cache ─────────────────────────────────────────────────────▶│
  │                   │ (miss)                                                             │
  │                   │ INSERT status=queued ─────▶│                     │                │
  │                   │ enqueue job ──────────────▶│                     │                │
  │◀── 200 {job_id} ──│                            │                     │                │
  │                   │                            │ pick up job         │                │
  │                   │                            │ UPDATE status=running ▶                │
  │                   │                            │ run 10 modules      │                │
  │                   │                            │ UPDATE result ─────▶│                │
  │                   │                            │ SETEX cache ─────────────────────────▶│
  │ GET /audit/{id} ─▶│ SELECT ───────────────────────────────────────▶ │                │
  │◀── 200 {...} ─────│                            │                     │                │
```

If Redis is down:

- `/audit/quick` still works (no queue involved).
- `/audit` returns `503 Service Unavailable`.
- `/audit/{job_id}` still works for previously-completed jobs (SQLite is the source of truth).
- The cache layer silently no-ops; log messages appear at DEBUG level.
