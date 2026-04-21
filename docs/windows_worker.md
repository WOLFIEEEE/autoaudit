# Windows NVDA worker

Path B of the screen-reader module (real NVDA speech capture) runs on a
Windows host. This guide walks through hooking a Windows laptop / VM up
to the Linux stack you deploy elsewhere (Coolify, Docker Compose, a
cloud VM — any topology that exposes Redis).

**Architecture recap:**

```
┌─────────────────────────────┐      Redis (Celery broker)      ┌──────────────────────────┐
│  Linux server + worker      │  ─────────────────────────────► │  Windows worker (laptop) │
│  (Coolify, Docker Compose)  │  ◄─────────────────────────────  │  queue=nvda              │
│  queue=default              │                                 │                          │
│  Path A + every other       │                                 │  Path B (real NVDA)      │
│  automated module           │                                 │                          │
└─────────────────────────────┘                                 └──────────────────────────┘
```

`audit.run` runs on the Linux worker. When its options say NVDA is
needed and we're not on Windows, it enqueues `audit.run_nvda` on the
`nvda` queue. Your Windows worker picks that up, runs NVDA, and merges
its findings back into the same audit row.

## Prerequisites (Windows side)

1. **Python 3.11 or newer** on `PATH`.
2. **Git** (if you're cloning the repo) or a copy of the project tree.
3. **NVDA**, installed from <https://www.nvaccess.org/download/> (free,
   open source). Run it once to accept the welcome dialog; the worker
   will auto-start it when a job arrives.
4. **Chromium** — install Playwright's browser bundle with:

       pip install -r requirements.txt
       playwright install chromium

5. **A way to reach Redis.** Tailscale is the easiest option; see below.

## Option A — Tailscale (recommended)

Gives you a private network between the Coolify host and the Windows
box, with zero port forwarding and no public Redis exposure.

1. **Install Tailscale** on both the Coolify host and the Windows
   laptop: <https://tailscale.com/download>. The free "Personal" tier
   is fine for one-digit device counts.
2. **Note the tailnet IP** of the Coolify host (run `tailscale ip` —
   you'll get something like `100.x.y.z`).
3. **Confirm Redis is bound on all interfaces** on the Coolify host. In
   Coolify, the Redis service defaults to exposing port 6379 only on
   the internal Docker network. You want it reachable from the
   tailnet IP too — the simplest route is to run Tailscale on the
   Coolify host itself (it sees the local Docker bridge) and make sure
   `REDIS_URL` on your services uses the internal name. From the
   Windows side, `100.x.y.z:6379` reaches it via tailscale's userspace
   networking.
4. **Set a Redis password.** Even on a tailnet, defense-in-depth is
   free. In `docker-compose.yml`:

   ```yaml
   redis:
     image: redis:7-alpine
     command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
   ```

   Then use `redis://:$REDIS_PASSWORD@100.x.y.z:6379/0` everywhere.

## Option B — Public Redis with TLS

Expose Redis on a public port with a strong password and TLS via
Traefik / stunnel. Higher attack surface. Only worth it if you already
manage a reverse proxy for Redis.

## Setup on the Windows laptop

Clone the repo and install deps:

```powershell
git clone <your-remote> autoaudit
cd autoaudit
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
playwright install chromium
```

Set environment variables. PowerShell:

```powershell
$env:REDIS_URL = "redis://:your-password@100.x.y.z:6379/0"
$env:SKIP_NVDA = "false"
$env:CELERY_QUEUES = "nvda"
$env:CELERY_POOL = "solo"
# Optional: shared SQLite for the NVDA worker to read existing audits
# from. Point at the same file your Linux worker writes, or leave as
# the default — the NVDA task only reads the audit blob back and
# writes an appendage; coordination is via Celery + Redis.
```

Start the worker (either script works):

```powershell
.\scripts\run_worker_windows.ps1
# or directly:
python scripts\run_worker.py
```

You should see a Celery banner listing `queues: nvda` and
`hostname: celery@<machine>-nvda`. Leave the terminal open.

## Install as a Windows service (always-on)

For a laptop you use day-to-day, install NSSM
(<https://nssm.cc/download>) so the worker survives restarts:

```powershell
# One-time install as Administrator.
nssm install AutoAuditNvda "C:\Path\To\Python\python.exe" "C:\repos\autoaudit\scripts\run_worker.py"
nssm set AutoAuditNvda AppDirectory "C:\repos\autoaudit"
nssm set AutoAuditNvda AppEnvironmentExtra ^
    REDIS_URL=redis://:your-password@100.x.y.z:6379/0 ^
    CELERY_QUEUES=nvda CELERY_POOL=solo SKIP_NVDA=false
nssm start AutoAuditNvda
```

Check status later with `nssm status AutoAuditNvda` or via
`services.msc`.

### Power settings

A laptop that goes to sleep stops serving the queue. In Windows
Settings → System → Power & battery:

- **Screen and sleep**: "When plugged in, never" for both.
- **Lid close action**: "Do nothing" (when plugged in).

This matters because Celery's `task_acks_late=True` means an NVDA job
picked up by a sleeping laptop is reinserted into the queue after a
broker timeout (one hour by default) — not ideal, but it does recover.

### Antivirus

Windows Defender sometimes quarantines Playwright browser downloads.
Exclude the repo folder: Settings → Privacy & security → Windows
Security → Virus & threat protection → Manage settings → Exclusions.

## Verification

From the Linux side, submit an audit with NVDA enabled:

```bash
curl -X POST https://your-server/audit \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com", "options": {"skip_nvda": false}}'
```

The response has `status: queued`. Poll:

```bash
curl https://your-server/audit/<job-id> | jq .nvda_status
```

Expect the lifecycle: `pending` → `completed`. If it sticks on
`pending`, the Windows worker isn't reaching Redis — check:

1. Is the worker process running? (`nssm status` or the PowerShell window)
2. Does the worker log line `connected to redis://...` at startup?
3. On the Linux host: `redis-cli -n 0 llen celery` — jobs pile up if
   nothing is consuming the `nvda` queue.

If it goes to `skipped` with reason `NVDA is only available on
Windows`, your worker's platform check is misbehaving (very unlikely
on a real Windows host).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|------------|-----|
| `nvda_status` stuck on `pending` forever | Worker not running, Redis unreachable | Check service, run `redis-cli -u $REDIS_URL ping` |
| `enqueue_failed` | Redis refused `apply_async` at enqueue time | Check REDIS_URL on *server*, not worker |
| Chrome / Playwright crashes | Missing browser binary | `playwright install chromium` |
| Works interactively, fails as service | Env vars not set for service account | Use `nssm set ... AppEnvironmentExtra` |
| Audit finishes but no NVDA issues appear | Path B still stubbed in `audit/screen_reader.py` | Expected until the NVDAController real implementation lands |

## Scaling past one laptop

Once you outgrow a single laptop, the same script works unchanged on:

- A Windows mini-PC (e.g. Beelink, Intel NUC) kept in a closet.
- A Windows VM in Azure / AWS (cheapest: Azure `B1s` at ~$20/mo).
- A Parallels / UTM VM on a Mac Mini acting as a 24/7 server.

All three just need the same `REDIS_URL` and `CELERY_QUEUES=nvda`.
The broker handles work distribution across multiple NVDA workers
natively — no additional configuration on the Linux side.
