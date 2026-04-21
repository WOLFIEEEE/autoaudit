# scripts/run_worker_windows.ps1
#
# One-click Windows launcher for the NVDA (Path B) worker.
# Intended to be run from the repo root on a Windows laptop/desktop.
#
# Usage:
#   .\scripts\run_worker_windows.ps1
#
# Prereqs (see docs/windows_worker.md for the full setup guide):
#   - Python 3.11+ on PATH
#   - `pip install -r requirements.txt`
#   - `playwright install chromium` (for Chromium a11y-tree sanity)
#   - NVDA installed from nvaccess.org
#   - Redis reachable at $env:REDIS_URL (Tailscale recommended)
#
# Env overrides:
#   $env:REDIS_URL       Redis URL. Required.
#   $env:DATABASE_URL    Ignored on the NVDA worker — it only touches Redis
#                        for task coordination and never writes SQLite
#                        directly (it calls back into server.database via
#                        network? No: see docs — for now the shared DB is
#                        assumed reachable at the same path via a mounted
#                        volume or shared filesystem).
#   $env:CELERY_QUEUES   Defaults to "nvda".

param(
  [string]$Queues = $env:CELERY_QUEUES,
  [string]$LogLevel = $env:CELERY_LOGLEVEL
)

$ErrorActionPreference = "Stop"

if (-not $env:REDIS_URL) {
  Write-Error "REDIS_URL is not set. Point it at your Coolify Redis (see docs/windows_worker.md)."
  exit 1
}

# Default to NVDA-only on the Windows box.
if (-not $Queues) { $Queues = "nvda" }
if (-not $LogLevel) { $LogLevel = "INFO" }

$env:CELERY_QUEUES = $Queues
$env:CELERY_POOL = "solo"          # Required on Windows.
$env:CELERY_LOGLEVEL = $LogLevel
# NVDA lives here — don't let the default-skip kick in on Windows.
$env:SKIP_NVDA = "false"

Write-Host "Starting NVDA worker (queues=$Queues, pool=solo, loglevel=$LogLevel)"
Write-Host "Redis: $($env:REDIS_URL -replace ':[^:@]*@', ':****@')"

python scripts\run_worker.py
