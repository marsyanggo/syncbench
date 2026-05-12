# Setup an ATF agent on Windows 10 / Windows 11.
# Run this script ON the target Windows machine after cloning the repo.
# MUST be run as Administrator (required for firewall rule creation).
#
# Usage:
#   # Right-click PowerShell -> "Run as Administrator", then:
#   .\scripts\setup-windows.ps1
#   .\scripts\setup-windows.ps1 -Broker atf-broker.local -AgentId win-nb-01
#
# What it does:
#   0. Verify Administrator privileges
#   1. Install uv (Astral-sh.uv) + iperf3 via winget (two-id fallback, then manual)
#   2. cd to repo root + uv sync
#   3. Add Windows Firewall rules for iperf3 TCP + UDP port 5201 (idempotent)
#   4. Smoke test: start agent 3s, check log for "State.*BOOT"
#   5. Print success banner with launcher path + example run command
#
# Differences from setup-macos.sh / setup-linux.sh:
#   - winget instead of Homebrew / apt
#   - No LaunchAgent or systemd; user manually runs scripts\run-agent.ps1
#     per test session (deliberate -- keeps agent visible in Task Manager)
#   - Firewall rule required; macOS / Linux open port by default in test env
#   - iperf3 PATH update takes effect in NEW PowerShell session after winget install
#   - PowerShell 5.1 (Windows built-in) -- no PowerShell 7 assumption
#   - $ErrorActionPreference = "Stop" catches terminating errors from cmdlets;
#     winget exits with non-zero for "already installed" (exit 0 = success,
#     exit -1978335189 / 0x8A15002B = already installed, treated as OK)
#   - No emoji (PowerShell 5.1 console encoding is not reliably UTF-8)
#
# Test environment notes:
#   - Set High-Performance power plan to prevent Wi-Fi adapter sleep:
#       powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
#   - Disable USB selective suspend in Device Manager or via powercfg for
#     USB Wi-Fi adapters
#   - Connect to the test SSID BEFORE running setup (Wi-Fi settings)
#   - Do NOT call Set-ExecutionPolicy in this script; user or admin handles policy
#   - Open a fresh PowerShell session after setup so winget PATH additions take effect

param(
    [string]$Broker  = "atf-broker.local",
    [string]$AgentId = "win-nb-01"
)
$ErrorActionPreference = "Stop"

# ── Helper output functions ───────────────────────────────────────────────────

function Write-OK   { param([string]$Msg) Write-Host "OK  $Msg" }
function Write-Warn { param([string]$Msg) Write-Host "WARN $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "X   $Msg" -ForegroundColor Red }

function Write-Step {
    param([string]$Heading)
    Write-Host ""
    Write-Host "# ── $Heading"
}

# ── 0. Administrator check ───────────────────────────────────────────────────
Write-Step "0. Administrator check"

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Fail "Not running as Administrator"
    Write-Host "  Right-click PowerShell -> 'Run as Administrator' and re-run:" -ForegroundColor Yellow
    Write-Host "  .\scripts\setup-windows.ps1 -Broker $Broker -AgentId $AgentId"
    exit 1
}
Write-OK "Administrator privileges confirmed"

# ── 1. winget packages: uv + iperf3 ─────────────────────────────────────────
Write-Step "1. winget packages (uv + iperf3)"

# Install uv
Write-Host "  Installing uv (Astral-sh.uv)..."
# winget exit code 0 = success; -1978335189 (0x8A15002B) = already installed -- both are OK
$uvResult = Start-Process -FilePath "winget" `
    -ArgumentList @("install", "--id", "Astral-sh.uv",
                    "--accept-source-agreements", "--accept-package-agreements", "--silent") `
    -Wait -PassThru -NoNewWindow
if ($uvResult.ExitCode -eq 0 -or $uvResult.ExitCode -eq -1978335189) {
    Write-OK "uv installed (or already present)"
} else {
    Write-Fail "winget install uv failed (exit $($uvResult.ExitCode))"
    Write-Host "  Install uv manually: https://github.com/astral-sh/uv/releases" -ForegroundColor Yellow
    # Non-fatal: uv may already be on PATH from a prior install
}

# Install iperf3 -- try two known winget IDs, then prompt for manual install
# TODO: verify exact winget ID on test machine ('winget search iperf3')
Write-Host "  Installing iperf3..."
$iperf3Installed = $false
foreach ($pkgId in @("ar51an.iperf3-win-builds", "iPerf.iPerf3")) {
    Write-Host "    Trying winget id: $pkgId ..."
    $r = Start-Process -FilePath "winget" `
        -ArgumentList @("install", "--id", $pkgId,
                        "--accept-source-agreements", "--accept-package-agreements", "--silent") `
        -Wait -PassThru -NoNewWindow
    if ($r.ExitCode -eq 0 -or $r.ExitCode -eq -1978335189) {
        Write-OK "iperf3 installed via winget ($pkgId)"
        $iperf3Installed = $true
        break
    } else {
        Write-Host "    winget $pkgId not found (exit $($r.ExitCode)), trying next..." -ForegroundColor Yellow
    }
}

if (-not $iperf3Installed) {
    Write-Warn "iperf3 not available via winget -- please install manually:"
    Write-Host "  1. Download from https://iperf.fr/iperf-download.php" -ForegroundColor Yellow
    Write-Host "  2. Extract iperf3.exe to a folder on your PATH" -ForegroundColor Yellow
    Write-Host "     (e.g. C:\Tools\iperf3\  then add to System PATH)" -ForegroundColor Yellow
    Write-Host "  3. Verify: iperf3 --version" -ForegroundColor Yellow
    Write-Host "  Press Enter once done, or Ctrl+C to abort..." -ForegroundColor Yellow
    Read-Host
}

# ── 2. uv sync ───────────────────────────────────────────────────────────────
Write-Step "2. Python dependencies (uv sync)"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location "$RepoRoot"
Write-Host "  Repo root: $RepoRoot"
Write-Host "  Running uv sync..."
uv sync
Write-OK "uv sync complete"

# ── 3. Windows Firewall rules (idempotent) ───────────────────────────────────
Write-Step "3. Firewall rules (iperf3 TCP + UDP 5201)"

$rules = @(
    @{ Name = "ATF iperf3 TCP 5201"; Protocol = "TCP" },
    @{ Name = "ATF iperf3 UDP 5201"; Protocol = "UDP" }
)

foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-OK "Firewall rule already exists: $($r.Name)"
    } else {
        # Scope: Domain + Private only (not Public — avoid exposing port 5201
        # when the laptop is on coffee-shop / airport Wi-Fi).
        # RemoteAddress: LocalSubnet only (orchestrator is on the same lab LAN).
        New-NetFirewallRule `
            -DisplayName   $r.Name `
            -Direction     Inbound `
            -Protocol      $r.Protocol `
            -LocalPort     5201 `
            -Action        Allow `
            -Profile       Domain,Private `
            -RemoteAddress LocalSubnet | Out-Null
        Write-OK "Created firewall rule: $($r.Name) (Domain+Private, LocalSubnet only)"
    }
}

# ── 4. Smoke test ────────────────────────────────────────────────────────────
Write-Step "4. Smoke test (3s)"

$logFile    = [System.IO.Path]::GetTempFileName()
$logFileErr = [System.IO.Path]::GetTempFileName()

Write-Host "  Starting agent for 3 s (log: $logFile) ..."
$proc = Start-Process -FilePath "uv" `
    -ArgumentList @("run", "atf-agent", "--broker", $Broker, "--agent-id", $AgentId) `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError  $logFileErr `
    -PassThru -NoNewWindow
Start-Sleep -Seconds 3

if (-not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
}

$logContent = (Get-Content $logFile -ErrorAction SilentlyContinue) -join "`n"
if ($logContent -match "State.*BOOT") {
    Write-OK "Agent starts (saw 'State -> BOOT' in log)"
} else {
    Write-Warn "Smoke test inconclusive (broker may not be reachable yet)"
    Write-Host "  Stdout log : $logFile" -ForegroundColor Yellow
    Write-Host "  Stderr log : $logFileErr" -ForegroundColor Yellow
    Write-Host "  To inspect : Get-Content '$logFile'" -ForegroundColor Yellow
}

# ── 5. Success banner ────────────────────────────────────────────────────────
Write-Step "5. Done"

$launcher = Join-Path $RepoRoot "scripts\run-agent.ps1"

Write-Host ""
Write-Host "============================================================"
Write-Host "  Setup complete!"
Write-Host "  Agent   : $AgentId"
Write-Host "  Broker  : $Broker"
Write-Host "  Launcher: $launcher"
Write-Host ""
Write-Host "  To run the agent (no admin required):"
Write-Host "  & '$launcher' -Broker $Broker -AgentId $AgentId"
Write-Host ""
Write-Host "  NOTE: Open a NEW PowerShell session first so that winget"
Write-Host "        PATH additions (uv, iperf3) take effect."
Write-Host "============================================================"
