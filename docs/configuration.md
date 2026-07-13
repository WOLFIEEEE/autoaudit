# Configuration reference

Everything the server reads from its environment, and every per-audit option a client can send.

- [Environment variables](#environment-variables)
- [Per-audit options](#per-audit-options)
- [Deployment modes](#deployment-modes)

---

## Environment variables

All server/worker configuration is env-var driven. See [server/config.py](../server/config.py) for the loader.

| Variable              | Default                                                      | Purpose |
|-----------------------|--------------------------------------------------------------|---------|
| `REDIS_URL`           | `redis://localhost:6379/0`                                   | Celery broker + result backend and result cache |
| `DATABASE_URL`        | `sqlite:///./data/audits.db`                                 | Audit result storage. Relative paths resolve against the project root |
| `AXE_SCRIPT_PATH`     | `vendor/axe.min.js`                                          | Local axe-core bundle. Relative paths resolve against the project root. Loaded first if present |
| `AXE_CDN_URL`         | `https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.12.1/axe.min.js` | Checksum-verified fallback used only by `scripts/fetch_axe.py`; audits require a local vendor file |
| `SKIP_NVDA`           | `""` (false)                                                 | When `1`/`true`/`yes`, skip Path B (real NVDA) even on Windows. Non-Windows hosts always skip |
| `CACHE_TTL_SECONDS`   | `900`                                                        | Redis cache TTL for equivalent URL + option audit results |
| `CACHE_ENABLED`       | `true`                                                       | Set false to disable only the optional result cache |
| `REDIS_REQUIRED`      | `false`                                                      | Make `/health` fail when Redis is unavailable (enabled in Docker Compose) |
| `MAX_AUDIT_SECONDS`   | `180`                                                        | Celery soft time limit. Hard limit is +30s |
| `API_KEYS`            | `""` (disabled)                                              | Comma-separated list of accepted keys. When set, every endpoint except `/health` and the docs endpoints requires `X-API-Key` or `Authorization: Bearer <key>` |
| `RATE_LIMIT_PER_MIN`  | `0` (disabled)                                               | Per-key sliding-window rate limit. Key is the API key ID when auth is on, client IP otherwise |
| `LOG_LEVEL`           | `INFO`                                                       | Standard logging level name |
| `LOG_FORMAT`          | `text`                                                       | `json` for line-per-record structured logs, anything else for human-readable |

### Celery worker knobs

The provided `scripts/run_worker.py` uses `--pool=solo`, which:

- Runs tasks serially in a single process.
- Is required on Windows (Celery's default prefork pool doesn't work there).
- Is appropriate for Playwright anyway — each audit launches a browser, so concurrent tasks on the same worker would contend for OS resources.

To scale, run multiple workers (each with `--pool=solo`) rather than increasing concurrency within a single worker.

### Database

SQLite is the default. Change `DATABASE_URL` to a Postgres URL (`postgresql://user:pass@host/db`) to swap backends — the `server/database.py` layer uses `sqlite3` directly, so a Postgres swap requires replacing that module. Tracked as a future enhancement.

#### Cleaning up old results

SQLite is append-only; the table grows forever unless you prune it. A CLI lives at `scripts/cleanup_audits.py`:

```bash
# Delete completed / failed audits older than 30 days
python scripts/cleanup_audits.py --days 30

# Dry run — report the count without deleting
python scripts/cleanup_audits.py --days 30 --dry-run
```

Wire this into a cron, systemd timer, k8s CronJob, or Celery beat task. Queued or running audits are never deleted — if they're stale, an operator should investigate.

### `SKIP_NVDA` resolution

The `skip_nvda` option resolution order (highest precedence first):

1. Explicit `options.skip_nvda` in the request body (bool).
2. `SKIP_NVDA` environment variable.
3. Platform default: `True` on non-Windows, `False` on Windows.

Path A (Chromium a11y-tree) **always** runs regardless of this setting. `skip_nvda` only gates the Path B overlay.

---

## Per-audit options

The `options` object in the request body. All fields are optional; defaults are shown in [docs/api.md's AuditOptions schema](api.md#auditoptions).

### `level` — WCAG compliance level

- `"a"`, `"aa"` (default), `"aaa"`.
- Passed to axe-core as the `runOnly.values` tag set:
  - `a` → `wcag2a`, `wcag21a`, `wcag22a`
  - `aa` → plus `wcag2aa`, `wcag21aa`, `wcag22aa`
  - `aaa` → plus `wcag2aaa`, `wcag21aaa`, `wcag22aaa`
- Custom (non-axe) rules don't filter by level; they fire based on their individual WCAG mapping.

### `skip_nvda` — disable Path B

See the env-var section above. Path A a11y-tree rules always run.

### `wait_ms` — Tab walk delay

Milliseconds to wait after each `Tab` press in the keyboard module. Lower = faster, higher = more reliable on pages with async focus handlers. Default `400`. Tests use `10`.

### `max_tabs` — Tab walk upper bound

Cap on the number of `Tab` presses. Hitting this cap without focus cycling or leaving the page fires `keyboard-trap-suspected`. Default `500`.

### `viewport` — browser viewport

`{ width, height }`. Default `{ width: 1280, height: 720 }`. Passed to `BrowserContext` at creation. The `responsive-target-size` rule measures against this viewport.

### `cookies` — pre-set cookies

Array of cookie objects in Playwright's [AddCookie format](https://playwright.dev/docs/api/class-browsercontext#browser-context-add-cookies). Useful for auditing authenticated pages:

```json
{
    "cookies": [
        {
            "name": "session",
            "value": "abc123",
            "domain": ".example.com",
            "path": "/",
            "httpOnly": true,
            "secure": true
        }
    ]
}
```

### `headers` — extra HTTP headers

Flat `{ name: string }` dict sent with every request. Typical use: API keys or feature flags.

### `basic_auth` — HTTP basic credentials

`{ username, password }`. Applied at the browser context level; Playwright handles the 401 challenge automatically.

### `timeout_seconds` — per-page navigation timeout

Default `120`. Applied to the initial `page.goto(...)`; the whole audit is additionally bounded by `MAX_AUDIT_SECONDS` at the Celery level.

---

## Deployment modes

### All-in-one (development)

`python main.py` in one terminal, `python scripts/run_worker.py` in another, Redis in a third. Use for local iteration.

### Docker compose (Linux production)

`docker compose up --build` boots `server`, `worker`, and `redis`. SQLite data is persisted in `./data`. Path B is not available in this mode — `SKIP_NVDA=true` is set in the compose file.

### Hybrid (Linux + Windows NVDA worker)

The plan's third deployment option. The server and Redis live in Linux Docker; the Windows worker is a separate machine running:

```powershell
$env:REDIS_URL = "redis://linux-host:6379/0"
$env:SKIP_NVDA = "false"
python scripts\run_worker.py
```

Path B is not yet implemented. When it lands, this doc will gain a **queue routing** section — the API will send `skip_nvda=false` jobs to a dedicated `audit.nvda` queue that only the Windows worker subscribes to. See [docs/architecture.md](architecture.md#path-b-real-nvda-worker) for the design.
