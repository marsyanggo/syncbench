#!/bin/bash
# Setup an ATF agent on macOS (Apple Silicon, macOS 14+).
# Run this script ON the target Mac after cloning the repo.
#
# Usage:
#   bash scripts/setup-macos.sh \
#     [--broker <host>] [--agent-id <id>]
#
# What it does:
#   1. Installs Homebrew (if missing)
#   2. Installs iperf3 + uv via brew
#   3. uv sync
#   4. Smoke check
#   5. Installs LaunchAgent at ~/Library/LaunchAgents/com.atf.agent.plist
#
# Differences from setup-linux.sh:
#   - Homebrew instead of apt
#   - LaunchAgent (user-level) instead of systemd
#   - No nmcli — connect to test SSID manually beforehand (System Settings → Wi-Fi)
#   - No iw power-save toggle — Apple does not expose the knob
#   - No hostname change — agent_id is independent of system hostname
#
# Test environment:
#   - For consistent throughput: keep the Mac plugged in (battery → power-save kicks in)
#   - Disable App Nap for reliable background runs:
#       defaults write NSGlobalDomain NSAppSleepDisabled -bool YES

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BROKER="atf-broker.local"
AGENT_ID="mac-nb-01"

while [[ $# -gt 0 ]]; do
    case $1 in
        --broker)   BROKER="$2";   shift 2 ;;
        --agent-id) AGENT_ID="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

_step() { echo; echo "── $1"; }
_ok()   { echo "  ✓ $1"; }
_warn() { echo "  ⚠ $1"; }

# ── 0. Sanity ────────────────────────────────────────────────────────
_step "0. Sanity"
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "  ✗ Not macOS — use scripts/setup-linux.sh instead"
    exit 1
fi
_ok "macOS $(sw_vers -productVersion)"
_ok "arch $(uname -m)"

# ── 1. Homebrew ──────────────────────────────────────────────────────
_step "1. Homebrew"
# Source shellenv first — non-login shells (e.g. ssh non-interactive) don't
# inherit /opt/homebrew/bin in PATH even when brew is installed.
if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
elif ! command -v brew &>/dev/null; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -d /opt/homebrew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi
_ok "brew $(brew --version | head -1)"

# ── 2. iperf3 + uv ───────────────────────────────────────────────────
_step "2. iperf3 + uv"
brew install --quiet iperf3 uv
_ok "iperf3 $(iperf3 --version 2>&1 | head -1)"
_ok "uv $(uv --version)"

# ── 3. Python deps ───────────────────────────────────────────────────
_step "3. Python deps"
cd "$REPO_DIR"
uv sync --quiet
_ok "uv sync complete"

# ── 4. Smoke check ───────────────────────────────────────────────────
_step "4. Smoke check"
timeout 3 uv run atf-agent --broker "$BROKER" --agent-id "$AGENT_ID" \
    > /tmp/atf-agent-check.log 2>&1 || true
if grep -q "State → BOOT" /tmp/atf-agent-check.log 2>/dev/null; then
    _ok "Agent starts OK"
else
    _warn "Agent check inconclusive (broker may not be reachable yet)"
    echo "    Check: cat /tmp/atf-agent-check.log"
fi

# ── 5. LaunchAgent ───────────────────────────────────────────────────
_step "5. LaunchAgent (com.atf.agent)"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/com.atf.agent.plist"
UV_BIN="$(command -v uv)"
mkdir -p "$PLIST_DIR"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.atf.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>$UV_BIN</string>
        <string>run</string>
        <string>atf-agent</string>
        <string>--broker</string>
        <string>$BROKER</string>
        <string>--agent-id</string>
        <string>$AGENT_ID</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/atf-agent.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/atf-agent.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

# Reload: bootout if loaded, then bootstrap (modern launchctl idiom)
launchctl bootout "gui/$UID/com.atf.agent" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
_ok "LaunchAgent installed + loaded"

echo
echo "══════════════════════════════════════════"
echo "  Setup complete!"
echo "  Agent  : $AGENT_ID"
echo "  Broker : $BROKER"
echo "  Plist  : $PLIST"
echo ""
echo "  Status : launchctl list | grep com.atf.agent"
echo "  Logs   : tail -f /tmp/atf-agent.out.log"
echo "  Stop   : launchctl bootout gui/\$UID/com.atf.agent"
echo "  Start  : launchctl bootstrap gui/\$UID $PLIST"
echo "══════════════════════════════════════════"
