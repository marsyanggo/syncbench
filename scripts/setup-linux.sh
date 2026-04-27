#!/bin/bash
# Setup an ATF agent on any Debian-based Linux device (Raspberry Pi, laptop, NUC, etc.).
# Run this script ON the target device after rsync.
#
# Usage:
#   bash ~/atf-validator/scripts/setup-linux.sh \
#     [--broker <host>] [--agent-id <id>] [--wifi-ssid <ssid>] [--wifi-pass <pass>]
#
# What it does:
#   0. Set hostname → <agent-id>
#   1. (optional) Connect to Wi-Fi via NetworkManager
#   2. (optional) Disable Wi-Fi power save (critical for laptops)
#   3. Install system packages (iperf3, iw, chrony)
#   4. Install uv + Python 3.11
#   5. uv sync
#   6. Install systemd service (auto-start on boot)
#
# Notes for laptop / desktop deployment:
#   - Pass --wifi-ssid / --wifi-pass to auto-join the test SSID via nmcli
#   - Power save is disabled to prevent throughput dips during long iperf3 runs
#   - Disable laptop sleep/lid-close suspend separately:
#       sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BROKER="atf-broker.local"
AGENT_ID="rpi-sta-01"
WIFI_SSID=""
WIFI_PASS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --broker)    BROKER="$2";    shift 2 ;;
        --agent-id)  AGENT_ID="$2";  shift 2 ;;
        --wifi-ssid) WIFI_SSID="$2"; shift 2 ;;
        --wifi-pass) WIFI_PASS="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

_step() { echo; echo "── $1"; }
_ok()   { echo "  ✓ $1"; }
_warn() { echo "  ⚠ $1"; }

# ── 0. Hostname ───────────────────────────────────────────────────────
_step "0. Hostname → $AGENT_ID"
OLD_HOST=$(hostname)
sudo hostnamectl set-hostname "$AGENT_ID"
sudo sed -i "s/$OLD_HOST/$AGENT_ID/g" /etc/hosts 2>/dev/null || true
_ok "hostname set (reachable as $AGENT_ID.local after reboot)"

# ── 1. Wi-Fi (optional) ───────────────────────────────────────────────
if [[ -n "$WIFI_SSID" ]]; then
    _step "1. Wi-Fi → $WIFI_SSID"
    if command -v nmcli &>/dev/null; then
        sudo nmcli dev wifi connect "$WIFI_SSID" password "$WIFI_PASS" || _warn "nmcli connect failed"
        _ok "connected via NetworkManager"
    else
        _warn "nmcli not found — connect to $WIFI_SSID manually before continuing"
    fi
else
    _step "1. Wi-Fi (skipped, use --wifi-ssid to auto-connect)"
fi

# ── 2. Disable Wi-Fi power save (laptop critical) ────────────────────
_step "2. Disable Wi-Fi power save"
WIFI_IFACE=$(iw dev 2>/dev/null | awk '/Interface/ {print $2; exit}')
if [[ -n "$WIFI_IFACE" ]]; then
    sudo iw dev "$WIFI_IFACE" set power_save off 2>/dev/null && \
        _ok "$WIFI_IFACE power_save off" || \
        _warn "could not disable power_save on $WIFI_IFACE"
    # Persist via NetworkManager if available
    if command -v nmcli &>/dev/null; then
        sudo nmcli connection modify "$(nmcli -t -f NAME con show --active | grep -v lo | head -1)" \
            802-11-wireless.powersave 2 2>/dev/null || true
    fi
else
    _warn "no Wi-Fi interface detected"
fi

# ── 3. System packages ────────────────────────────────────────────────
_step "3. System packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    iperf3 iw chrony curl ca-certificates
_ok "iperf3 $(iperf3 --version 2>&1 | head -1)"
_ok "iw $(iw --version 2>/dev/null || echo 'installed')"
_ok "chrony installed"

# ── 4. uv + Python 3.11 ──────────────────────────────────────────────
_step "4. uv + Python 3.11"
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
fi
uv python install 3.11 --quiet
_ok "uv $(uv --version)"
_ok "python $(uv run python --version)"

# ── 5. Python dependencies ───────────────────────────────────────────
_step "5. Python dependencies"
cd "$REPO_DIR"
uv sync --quiet
_ok "uv sync complete"

# ── 6. Verify agent starts ───────────────────────────────────────────
_step "6. Smoke check"
timeout 3 uv run atf-agent --broker "$BROKER" --agent-id "$AGENT_ID" \
    > /tmp/atf-agent-check.log 2>&1 || true
if grep -q "State → BOOT" /tmp/atf-agent-check.log 2>/dev/null; then
    _ok "Agent starts OK"
else
    _warn "Agent check inconclusive (broker may not be reachable yet)"
    echo "    Check: cat /tmp/atf-agent-check.log"
fi

# ── 7. systemd service ───────────────────────────────────────────────
_step "7. systemd service (atf-agent)"
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

echo
echo "══════════════════════════════════════════"
echo "  Setup complete!"
echo "  Host    : $AGENT_ID"
echo "  Broker  : $BROKER"
echo "  Wi-Fi   : ${WIFI_IFACE:-unknown} (power_save off)"
echo ""
echo "  Start now : sudo systemctl start atf-agent"
echo "  Logs      : journalctl -u atf-agent -f"
echo "══════════════════════════════════════════"
