# Manual ATF agent launcher for Windows.
# Run this script before each test session to start the ATF agent.
# Does NOT require Administrator privileges (firewall rules are pre-installed
# by scripts\setup-windows.ps1 which must be run once as Administrator).
#
# Usage:
#   .\scripts\run-agent.ps1
#   .\scripts\run-agent.ps1 -Broker atf-broker.local -AgentId win-nb-01
#   # Or from any directory:
#   & 'C:\path\to\repo\scripts\run-agent.ps1' -Broker atf-broker.local -AgentId win-nb-01
#
# What it does:
#   1. Resolves the repo root via $PSScriptRoot (works from any cwd)
#   2. Sets the working directory to the repo root
#   3. Runs: uv run atf-agent --broker <Broker> --agent-id <AgentId>
#   4. Exits when the agent exits (Ctrl+C to stop cleanly)
#
# Differences from macOS/Linux equivalent:
#   - macOS uses a LaunchAgent plist (auto-start on login); this is intentionally
#     a manual launcher to keep the agent visible in Task Manager
#   - Linux uses systemd; no equivalent here (Task Scheduler intentionally avoided)
#   - No trap/signal handler needed: Ctrl+C in PowerShell terminates the child
#     process cleanly via the console control handler
#   - Working directory is resolved via Split-Path -Parent $PSScriptRoot, NOT via
#     "$(dirname $0)/.." (that is bash syntax)
#
# Test environment notes:
#   - Ensure you have run .\scripts\setup-windows.ps1 at least once (as Admin)
#     so firewall rules and Python deps are in place
#   - Open a fresh PowerShell session after setup so PATH changes take effect
#   - The agent writes logs to stdout; pipe to a file if you need persistent logs:
#       .\scripts\run-agent.ps1 | Tee-Object -FilePath C:\Temp\atf-agent.log
#   - For unattended runs consider: Start-Process -FilePath "pwsh" -ArgumentList
#     "-File .\scripts\run-agent.ps1" -RedirectStandardOutput "C:\Temp\atf.log"

param(
    [string]$Broker  = "atf-broker.local",
    [string]$AgentId = "win-nb-01"
)
$ErrorActionPreference = "Stop"

# ── Resolve repo root (script lives in <repo>\scripts\) ──────────────────────
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location "$RepoRoot"

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host "Starting ATF agent..."
Write-Host "  Broker  : $Broker"
Write-Host "  AgentId : $AgentId"
Write-Host "  CWD     : $RepoRoot"
Write-Host ""
Write-Host "  Press Ctrl+C to stop the agent."
Write-Host ""

# ── Launch (blocking; Ctrl+C terminates the child via console control) ────────
uv run atf-agent --broker "$Broker" --agent-id "$AgentId"
