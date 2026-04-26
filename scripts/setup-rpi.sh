#!/bin/bash
# Run this script ON the RPi after rsync.
# Usage: bash ~/atf-validator/scripts/setup-rpi.sh [--broker <ip>] [--agent-id <id>]
#
# What it does:
#   1. Install system packages (iperf3, iw, chrony)
#   2. Install uv + Python 3.11
#   3. uv sync (install Python deps)
#   4. Install systemd service (auto-start on boot)

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BROKER="atf-broker.local"
AGENT_ID="rpi-sta-01"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --broker)   BROKER="$2";   shift 2 ;;
        --agent-id) AGENT_ID="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

_step() { echo; echo "── $1"; }
_ok()   { echo "  ✓ $1"; }

# ── 0. Hostname ───────────────────────────────────────────────────────
_step "0. Hostname → $AGENT_ID"
sudo hostnamectl set-hostname "$AGENT_ID"
sudo sed -i "s/raspberrypi/$AGENT_ID/g" /etc/hosts
_ok "hostname set to $AGENT_ID (reachable as $AGENT_ID.local after reboot)"

# ── 1. System packages ────────────────────────────────────────────────
_step "1. System packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    iperf3 iw chrony curl ca-certificates
_ok "iperf3 $(iperf3 --version 2>&1 | head -1)"
_ok "iw $(iw --version 2>/dev/null || echo 'installed')"
_ok "chrony installed"

# ── 2. uv + Python 3.11 ──────────────────────────────────────────────
_step "2. uv + Python 3.11"
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
fi
uv python install 3.11 --quiet
_ok "uv $(uv --version)"
_ok "python $(uv run python --version)"

# ── 3. Python dependencies ───────────────────────────────────────────
_step "3. Python dependencies"
cd "$REPO_DIR"
uv sync --quiet
_ok "uv sync complete"

# ── 4. Verify agent starts (quick check) ─────────────────────────────
_step "4. Smoke check"
timeout 3 uv run atf-agent --broker "$BROKER" --agent-id "$AGENT_ID" \
    > /tmp/atf-agent-check.log 2>&1 || true
if grep -q "State → BOOT" /tmp/atf-agent-check.log 2>/dev/null; then
    _ok "Agent starts OK"
else
    echo "  ⚠ Agent check inconclusive (broker may not be reachable yet)"
    echo "    Check: cat /tmp/atf-agent-check.log"
fi

# ── 5. systemd service ───────────────────────────────────────────────
_step "5. systemd service (atf-agent)"
SERVICE_FILE="/etc/systemd/system/atf-agent.service"
UV_BIN="$(command -v uv)"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=ATF Agent
After=network-online.target time-sync.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$REPO_DIR
ExecStart=$UV_BIN run atf-agent --broker $BROKER --agent-id $AGENT_ID
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable atf-agent
_ok "systemd service installed + enabled (atf-agent)"
_ok "Start: sudo systemctl start atf-agent"
_ok "Logs:  journalctl -u atf-agent -f"

echo
echo "══════════════════════════════════════════"
echo "  Setup complete!"
echo "  Broker : $BROKER"
echo "  Agent  : $AGENT_ID"
echo ""
echo "  To start now:"
echo "    sudo systemctl start atf-agent"
echo "  Or manually:"
echo "    uv run atf-agent --broker $BROKER --agent-id $AGENT_ID"
echo "══════════════════════════════════════════"
