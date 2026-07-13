# Register a Windows Scheduled Task that launches NVDA for autoaudit.
#
# NVDA's installed nvda.exe carries a requireAdministrator manifest, so
# a normal-privilege worker cannot start it via subprocess.Popen (it
# fails with WinError 740, "The requested operation requires elevation").
#
# This script creates a scheduled task with "Run with highest privileges"
# enabled. After that, any unelevated process can trigger NVDA on demand:
#     schtasks /Run /TN AutoauditNVDA
# That is exactly what audit.screen_reader.NVDAController falls back to
# when the direct launch is refused.
#
# Run this script ONCE, as administrator. It is idempotent: re-running
# will overwrite the existing task definition.
#
# Parameters:
#   -NvdaExe   Path to nvda.exe. Defaults to standard installer location.
#   -LogPath   Full path NVDA will write its log to. Must match what the
#              controller reads (default: %LOCALAPPDATA%\Temp\autoaudit_nvda.log).
#   -TaskName  Scheduled task name. Must match AUTOAUDIT_TASK_NAME in
#              audit/screen_reader.py (default: AutoauditNVDA).
#
# Example (from an elevated PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_nvda_task.ps1

[CmdletBinding()]
param(
    [string]$NvdaExe = "C:\Program Files (x86)\NVDA\nvda.exe",
    [string]$LogPath = "$env:LOCALAPPDATA\Temp\autoaudit_nvda.log",
    [string]$ConfigDir = "$env:LOCALAPPDATA\autoaudit\nvda-config",
    [string]$TaskName = "AutoauditNVDA"
)

$ErrorActionPreference = "Stop"

# -------- sanity checks --------------------------------------------------

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be run as Administrator. Right-click PowerShell and choose 'Run as administrator', then re-run."
    exit 1
}

if (-not (Test-Path -LiteralPath $NvdaExe)) {
    Write-Error "nvda.exe not found at '$NvdaExe'. Pass -NvdaExe <path> or install NVDA from https://www.nvaccess.org/"
    exit 1
}

Write-Host "Registering scheduled task '$TaskName'"
Write-Host "  nvda.exe  : $NvdaExe"
Write-Host "  log file  : $LogPath"
Write-Host "  config dir: $ConfigDir"

# Create the quiet config dir and seed nvda.ini so NVDA doesn't open
# its Welcome / usage-stats / update-check dialogs on first run. These
# can't be turned off via CLI flags — NVDA reads them from config.ini.
if (-not (Test-Path -LiteralPath $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
}
$iniPath = Join-Path $ConfigDir "nvda.ini"
@"
[general]
	showWelcomeDialogAtStartup = False
	saveConfigurationOnExit = False
[update]
	autoCheck = False
	askedAllowUsageStats = True
	startupNotification = False
[speechViewer]
	showSpeechViewerAtStartup = False
"@ | Set-Content -LiteralPath $iniPath -Encoding utf8
Write-Host "  wrote quiet config: $iniPath"

# -------- task definition ------------------------------------------------

# NVDA flags:
#   --log-level=12       DEBUG - emits the Speaking[...] lines we parse.
#   --minimal            no welcome dialog, no tray notification.
#   --log-file=<path>    write to a path the worker can read.
#   --no-logging-lowlevel-input    keep the log focused on speech events.
# schtasks.exe /TR needs the whole thing as ONE quoted string. Inner
# double-quotes (around $LogPath) are escaped with backslash which
# schtasks then unescapes into the stored task XML.
$innerTR = '\"' + $NvdaExe + '\" -c \"' + $ConfigDir + '\" --log-level=12 --minimal --log-file=\"' + $LogPath + '\" --no-logging-lowlevel-input'

# Why we call schtasks.exe instead of New-ScheduledTask* cmdlets:
#   - /NP (no password) creates an S4U-style task that runs without
#     storing credentials. Essential for accounts that use a Microsoft
#     account login or Windows Hello PIN, where there is no local
#     password the PowerShell cmdlets can validate.
#   - /RL HIGHEST still grants the task an elevated primary token at
#     run time because setup is performed from an admin shell.
#   - Combined effect: schtasks /Run (unelevated) launches NVDA
#     elevated, no UAC prompt, no password stored on disk.
#
# Remove any previous definition first so /Create doesn't conflict.
# Suppress stderr so "task not found" on first run isn't treated as a
# fatal error under $ErrorActionPreference = Stop.
$priorErrorPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
cmd.exe /c "schtasks.exe /Delete /TN $TaskName /F >nul 2>&1" | Out-Null

# schtasks.exe CLI doesn't accept /SC ONDEMAND (that's XML only). Use
# /SC ONCE with a far-future date so the task never auto-runs; we
# trigger it exclusively via `schtasks /Run`.
$createOutput = cmd.exe /c "schtasks.exe /Create /TN `"$TaskName`" /TR `"$innerTR`" /SC ONCE /ST 23:59 /SD 01/01/2099 /RL HIGHEST /RU `"$($currentUser.Name)`" /NP /F 2>&1"
$createExit = $LASTEXITCODE
$ErrorActionPreference = $priorErrorPref

if ($createExit -ne 0) {
    Write-Error "schtasks /Create failed (rc=$createExit). Output:`n$createOutput"
    exit 1
}
Write-Host $createOutput

Write-Host ""
Write-Host "OK. Scheduled task '$TaskName' registered." -ForegroundColor Green
Write-Host ""
Write-Host "Verify with:   schtasks /Query /TN $TaskName"
Write-Host "Trigger with:  schtasks /Run   /TN $TaskName"
Write-Host "Remove with:   schtasks /Delete /TN $TaskName /F"
Write-Host ""
Write-Host "If you move nvda.exe or the log path, re-run this script."
