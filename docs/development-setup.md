# Development Setup Guide

This guide covers everything needed to set up the syncbench development environment on macOS (Apple Silicon).

---

## Prerequisites

### 1. Homebrew

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Add to `~/.zshrc`:
```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 2. GPG (for signed commits)

```bash
brew install gnupg pinentry-mac
```

Generate a key:
```bash
gpg --full-generate-key
# Choose: RSA 4096, no expiry, your personal name + email
```

Configure pinentry:
```bash
echo "pinentry-program $(brew --prefix)/bin/pinentry-mac" >> ~/.gnupg/gpg-agent.conf
gpgconf --kill gpg-agent
```

Upload the public key to GitHub → Settings → GPG Keys:
```bash
gpg --armor --export <KEY_ID> | pbcopy
```

### 3. SSH Key (for GitHub)

```bash
ssh-keygen -t ed25519 -C "your@email.com" -f ~/.ssh/id_ed25519_personal
```

Configure `~/.ssh/config`:
```
Host github.com-personal
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_personal
    IdentitiesOnly yes
```

Upload the public key to GitHub → Settings → SSH Keys:
```bash
cat ~/.ssh/id_ed25519_personal.pub | pbcopy
```

Verify:
```bash
ssh -T git@github.com-personal
# Expected: Hi <username>! You've successfully authenticated...
```

### 4. Python (via uv)

```bash
brew install uv
uv python install 3.11
```

### 5. Docker Desktop

```bash
brew install --cask docker
```

Open **Docker.app** and wait until the menu bar whale icon shows "Docker Desktop is running".

### 6. Mosquitto CLI tools (for testing)

```bash
brew install mosquitto
```

These provide `mosquitto_pub` and `mosquitto_sub` for manual MQTT testing.
> Note: this installs the CLI tools only — the broker itself runs inside Docker.

---

## Project Setup

### Clone and install dependencies

```bash
git clone git@github.com-personal:marsyanggo/syncbench.git
cd syncbench
uv sync
```

### Git identity (repo-local, not global)

```bash
git config user.name "Your Name"
git config user.email "your@personal-email.com"
git config user.signingkey <GPG_KEY_ID>
git config commit.gpgsign true
git config gpg.program /opt/homebrew/bin/gpg
```

### Verify setup

```bash
# Python imports
uv run python -c "import paho.mqtt.client; import fastapi; import pydantic; import influxdb_client; print('OK')"

# Docker
docker compose version
```

---

## Start the infrastructure stack

```bash
docker compose up -d
```

Verify all three services:
```bash
# InfluxDB
curl localhost:8086/health
# Expected: {"status":"pass", ...}

# Mosquitto pub/sub roundtrip
mosquitto_sub -h localhost -p 1883 -t "atf/test" -C 1 &
mosquitto_pub -h localhost -p 1883 -t "atf/test" -m "hello-atf"
# Expected output: hello-atf

# Grafana
open http://localhost:3000
# Login: admin / atf-grafana-2026
```

> Grafana datasource (InfluxDB) and syncbench dashboard are auto-provisioned on first start via `deploy/grafana/`. No manual setup needed.

---

## Hardware Network Setup

### Topology

```
Mac mini ──Ethernet── AX4200 LAN (192.168.1.x)
RPi × N  ──5GHz Wi-Fi (atf_test_5g)── AX4200
```

### Mac mini — set mDNS hostname (one-time)

```bash
sudo scutil --set LocalHostName atf-broker
```

Verify: `ping atf-broker.local` should resolve (127.0.0.1 on Mac mini itself; actual LAN IP from RPi).

### AX4200 — enable 5GHz Wi-Fi (one-time, via SSH)

```bash
ssh root@192.168.1.1

uci set wireless.radio1.disabled='0'
uci set wireless.default_radio1.ssid='atf_test_5g'
uci set wireless.default_radio1.encryption='psk2+ccmp'
uci set wireless.default_radio1.key='12345678'
uci commit wireless
wifi up
```

> radio1 = 5GHz (MT7986A). radio0 = 2.4GHz (keep disabled for testing).

### Copy SSH key to RPi (one-time per RPi)

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_personal.pub <user>@raspberrypi.local
```

---

## RPi Agent Setup

### Deploy from Mac

```bash
# 1. Rsync repo to RPi
rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  -e "ssh -i ~/.ssh/id_ed25519_personal" \
  /path/to/syncbench/ <user>@raspberrypi.local:~/syncbench/

# 2. Run setup script on RPi (sets hostname, installs packages, configures systemd)
ssh -i ~/.ssh/id_ed25519_personal <user>@raspberrypi.local \
  "bash ~/syncbench/scripts/setup-linux.sh --broker atf-broker.local --agent-id rpi-sta-01"
```

`setup-linux.sh` will:
1. Set hostname to `rpi-sta-01` → reachable as `rpi-sta-01.local` after reboot
2. Install `iperf3`, `iw`, `chrony` via apt
3. Install `uv` + Python 3.11
4. Run `uv sync`
5. Install + enable `atf-agent` systemd service (broker: `atf-broker.local`)

> Each RPi gets a unique `--agent-id` (rpi-sta-01, rpi-sta-02, …). The ID is baked into the systemd service — RPi always knows who it is after reboot.

### Start the agent

```bash
# On RPi — via systemd (auto-restarts, survives reboot)
sudo systemctl start atf-agent
journalctl -u atf-agent -f

# Or manually
uv run atf-agent --broker atf-broker.local --agent-id rpi-sta-01
```

### Verify on Mac

Open Inspector: `uv run atf-inspector`  
Browser: `http://localhost:8080` → should show `● online  rpi-sta-01  IDLE`

---

## Running a test

### Single command — no manual setup needed

```bash
uv run atf-run scenarios/00_smoke_test.yaml       # 1 STA, 30s
uv run atf-run scenarios/01_two_sta_equal.yaml    # 2 STA, 60s
```

`atf-run` automatically:
1. Assigns a unique iperf3 port to each STA (5201, 5202, …)
2. Starts `iperf3 -s` locally for each port
3. Synchronises all agents to start at the same timestamp (`sleep_until` with busy-wait)
4. Streams per-second throughput to InfluxDB in real-time
5. Kills iperf3 servers and writes run summary when done

> No need to manually run `iperf3 -s` — the orchestrator handles it.

### Watch real-time in Grafana

1. Open `http://localhost:3000` → Dashboards → **syncbench**
2. Set time range to **Last 5 minutes**, auto-refresh **5s**
3. Run `atf-run` — lines appear in real-time as each STA reports throughput

### Run Inspector for live agent status

```bash
uv run atf-inspector   # then open http://localhost:8080
```

---

## Re-deploying code to RPis

After changing agent code on Mac, push to all RPis:

```bash
for ip in 192.168.1.221 192.168.1.233; do
  rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    -e "ssh -i ~/.ssh/id_ed25519_personal" \
    /path/to/syncbench/ mars@$ip:~/syncbench/
  ssh -i ~/.ssh/id_ed25519_personal mars@$ip "sudo systemctl restart atf-agent"
done
```

---

## Commit guidelines

- **No commits between 09:00–18:00 on workdays** (legal compliance)
- Every commit is GPG-signed automatically
- Use personal email only — never company email

See [design_spec/syncbench-phase1-spec.md](../design_spec/syncbench-phase1-spec.md) §15 for full legal compliance rules.
