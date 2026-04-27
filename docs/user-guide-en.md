# ATF Validator — User Guide

A complete guide to install, deploy, and operate the ATF (Airtime Fairness) Validator framework on a 1-AP / N-STA Wi-Fi testbed.

> Chinese version: [user-guide-zh.md](user-guide-zh.md)

---

## Table of Contents

1. [What is this?](#1-what-is-this)
2. [Architecture & Topology](#2-architecture--topology)
3. [Hardware Requirements](#3-hardware-requirements)
4. [Mac mini Setup (Controller)](#4-mac-mini-setup-controller)
5. [AX4200 Setup (Access Point)](#5-ax4200-setup-access-point)
6. [Raspberry Pi Setup (Stations)](#6-raspberry-pi-setup-stations)
7. [Running Tests](#7-running-tests)
8. [Viewing Results](#8-viewing-results)
9. [Re-deploying Code](#9-re-deploying-code)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What is this?

ATF Validator is a platform-agnostic framework for validating IEEE 802.11 Airtime Fairness on consumer Wi-Fi hardware. It coordinates multiple Raspberry Pi stations to run synchronized iperf3 traffic against a single AP, measures per-station throughput and start-time jitter, and visualizes results in real-time on Grafana.

**Phase 1 goals:**
- One-command execution: `atf-run scenarios/01_two_sta_equal.yaml`
- Real-time per-STA throughput curves in Grafana
- Sync precision < 100ms (typical: 0–1ms)
- Reproducible after system reboot within 5 minutes

The framework itself is transport-agnostic — only the ATF on/off toggle in test scenarios is Wi-Fi specific. The same orchestration pipeline works for wired QoS testing, multi-protocol traffic, or any synchronized multi-endpoint scenario.

---

## 2. Architecture & Topology

### Network topology

```
                ┌─────────────────────────┐
                │   Mac mini (Controller) │
                │   atf-broker.local      │
                │   192.168.1.x (DHCP)    │
                └────────────┬────────────┘
                             │ Ethernet
                             ▼
                ┌─────────────────────────┐
                │   AX4200 (OpenWrt AP)   │
                │   192.168.1.1           │
                │   SSID: atf_test_5g     │
                │   5GHz, ch36, HE80      │
                └────────────┬────────────┘
                             │ 5GHz Wi-Fi
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐
        │rpi-sta-01│   │rpi-sta-02│   │rpi-sta-NN│
        │.221      │   │.233      │   │.xxx      │
        └──────────┘   └──────────┘   └──────────┘
```

- **Mac mini** runs the controller, MQTT broker, InfluxDB, Grafana, iperf3 servers
- **AX4200** is the device-under-test (the AP whose ATF behavior we measure)
- **Raspberry Pis** are the stations that generate iperf3 traffic via Wi-Fi

### Software stack

| Layer | Component | Where it runs |
|---|---|---|
| Test scenarios | YAML files | Mac mini |
| Orchestrator | `atf-run` CLI | Mac mini |
| Inspector dashboard | FastAPI + SSE | Mac mini (`http://localhost:8080`) |
| Metrics dashboard | Grafana 11 | Mac mini (`http://localhost:3000`) |
| Time series DB | InfluxDB 2.7 | Mac mini |
| Message bus | Mosquitto MQTT 2.0 | Mac mini |
| Agent | `atf-agent` systemd service | Each RPi |
| Traffic | `iperf3` server (Mac) / client (RPi) | Both sides |

### Data flow

```
RPi iperf3 client ──per-second sample──▶ MQTT live topic
                                              │
Mac mini orchestrator ──subscribe──────────────┘
        │
        ├─▶ InfluxDB (real-time write)
        │       │
        │       ▼
        │   Grafana (5s refresh, lines drawn live)
        │
        └─▶ run_summary on completion
```

---

## 3. Hardware Requirements

| Item | Quantity | Spec |
|---|---|---|
| Mac mini (Apple Silicon) | 1 | macOS, ≥8GB RAM, Ethernet port |
| OpenWrt AP | 1 | ASUS AX4200 (MT7986A, mt76 driver) — supports `AIRTIME_FAIRNESS` + `AQL` |
| Raspberry Pi | 2–5 | RPi 4 / 400 / 500 with 5GHz Wi-Fi (Wi-Fi 6 ideal) |
| microSD cards | one per RPi | ≥16 GB, Class 10+ |
| Ethernet cable | 1 | Mac mini ↔ AX4200 LAN port |
| Power | per device | USB-C for RPi, barrel jack for AX4200 |

> 5 GHz is required (2.4 GHz is too noisy for repeatable results). Verify the AP's chipset supports ATF in mac80211 (mt76, ath9k, ath10k, ath11k all support it).

---

## 4. Mac mini Setup (Controller)

### 4.1 Install prerequisites

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc

# Tools
brew install uv mosquitto gnupg pinentry-mac iperf3
brew install --cask docker
uv python install 3.11
```

Open **Docker.app** and wait for the menu bar icon to show "Docker Desktop is running".

### 4.2 SSH + GPG keys (for GitHub commits)

```bash
ssh-keygen -t ed25519 -C "your@email.com" -f ~/.ssh/id_ed25519_personal
gpg --full-generate-key  # RSA 4096, no expiry
```

Upload public keys to GitHub → Settings → SSH Keys / GPG Keys.

### 4.3 Set Mac mini hostname for mDNS broker discovery

```bash
sudo scutil --set LocalHostName atf-broker
ping atf-broker.local  # should resolve
```

This makes the Mac mini reachable at `atf-broker.local` from any device on the LAN. RPis use this hostname to find the MQTT broker — no IP hard-coding.

### 4.4 Clone and install

```bash
git clone git@github.com:marsyanggo/syncbench.git
cd syncbench
uv sync
```

### 4.5 Start infrastructure

```bash
docker compose up -d
```

Verify:
```bash
curl localhost:8086/health           # InfluxDB → {"status":"pass",...}
docker compose ps                    # All three services Up
```

> Grafana datasource and ATF Validator dashboard are auto-provisioned on first start. No manual setup needed.

Open `http://localhost:3000` (admin / atf-grafana-2026) to verify Grafana is up.

---

## 5. AX4200 Setup (Access Point)

> One-time configuration. Skip if already done.

### 5.1 Connect Mac mini to AX4200 LAN port via Ethernet

Confirm the Mac mini's Ethernet IP is in the `192.168.1.x` range:
```bash
ifconfig en0 | grep inet
```

### 5.2 Enable 5 GHz Wi-Fi via SSH

```bash
ssh root@192.168.1.1

uci set wireless.radio1.disabled='0'
uci set wireless.default_radio1.ssid='atf_test_5g'
uci set wireless.default_radio1.encryption='psk2+ccmp'
uci set wireless.default_radio1.key='12345678'
uci commit wireless
wifi up
```

> `radio1` = 5 GHz (MT7986A). Leave `radio0` (2.4 GHz) disabled to avoid interference.

### 5.3 Verify ATF support

```bash
ssh root@192.168.1.1 "iw phy phy1 info | grep -i airtime"
# Expected: AIRTIME_FAIRNESS, AQL
```

### 5.4 Enable / disable ATF (for ATF on/off comparison tests, Week 4+)

```bash
# ATF on (dynamic mode)
ssh root@192.168.1.1 "uci set wireless.radio1.airtime_mode='2' && uci commit && wifi reload"

# ATF off
ssh root@192.168.1.1 "uci set wireless.radio1.airtime_mode='0' && uci commit && wifi reload"
```

---

## 6. Raspberry Pi Setup (Stations)

Repeat this section for each RPi. Use a unique `--agent-id` per RPi (`rpi-sta-01`, `rpi-sta-02`, …).

### 6.1 Flash Raspberry Pi OS Lite (64-bit, Bookworm or newer)

Use **Raspberry Pi Imager**:
- OS: Raspberry Pi OS Lite (64-bit)
- Set username/password during imaging (e.g., `mars` / `<your password>`)
- Enable SSH server
- Configure Wi-Fi: SSID `atf_test_5g`, password `12345678`

> If Wi-Fi was not configured during imaging, connect the RPi to any Wi-Fi network first, then SSH in and use `nmcli` or `wpa_supplicant` to switch to `atf_test_5g`.

### 6.2 Find the RPi's IP

After boot, the RPi joins the AX4200 network. From Mac mini:
```bash
ping raspberrypi.local
# OR check AX4200 DHCP leases:
ssh root@192.168.1.1 "cat /tmp/dhcp.leases"
```

### 6.3 Copy SSH key (one-time per RPi)

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_personal.pub mars@raspberrypi.local
# Enter the password set during imaging
```

### 6.4 Deploy and run setup script

```bash
RPI_IP=192.168.1.221      # the IP found in step 6.2
AGENT_ID=rpi-sta-01       # unique per RPi

# 1. Rsync code
rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  -e "ssh -i ~/.ssh/id_ed25519_personal" \
  ~/workspace/syncbench/ mars@$RPI_IP:~/syncbench/

# 2. Run setup
ssh -i ~/.ssh/id_ed25519_personal mars@$RPI_IP \
  "bash ~/syncbench/scripts/setup-linux.sh --broker atf-broker.local --agent-id $AGENT_ID"
```

`setup-linux.sh` automatically:
1. Sets hostname to `$AGENT_ID` → reachable as `<agent-id>.local` after reboot
2. Installs `iperf3`, `iw`, `chrony` via apt
3. Installs `uv` + Python 3.11
4. Runs `uv sync`
5. Installs and enables the `atf-agent` systemd service

### 6.5 Start the agent

```bash
ssh -i ~/.ssh/id_ed25519_personal mars@$RPI_IP "sudo systemctl start atf-agent"

# Watch logs
ssh -i ~/.ssh/id_ed25519_personal mars@$RPI_IP "journalctl -u atf-agent -f"
# Expected: State → BOOT → IDLE, NTP synced
```

### 6.6 Verify on Mac mini Inspector

```bash
uv run atf-inspector
# Open http://localhost:8080
# Should show: ● online  rpi-sta-01  IDLE  +0.0ms
```

### 6.7 Adding a Linux laptop or other device

The same `setup-linux.sh` works on any Debian-based device (Ubuntu/Debian laptop, NUC, etc.). For laptops, also pass Wi-Fi credentials to auto-join the test SSID and disable Wi-Fi power save:

```bash
NB_IP=192.168.1.245
AGENT_ID=linux-nb-01

rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  -e "ssh -i ~/.ssh/id_ed25519_personal" \
  ~/workspace/syncbench/ user@$NB_IP:~/syncbench/

ssh -i ~/.ssh/id_ed25519_personal user@$NB_IP \
  "bash ~/syncbench/scripts/setup-linux.sh \
     --broker atf-broker.local \
     --agent-id $AGENT_ID \
     --wifi-ssid atf_test_5g \
     --wifi-pass 12345678"
```

> Laptops also need suspend/lid-close disabled for long test runs:
> ```
> sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
> ```

For other platforms (Windows, Android, etc.) and the abstraction architecture, see [multi-platform.md](multi-platform.md).

---

## 7. Running Tests

### Single-command execution

```bash
uv run atf-run scenarios/01_two_sta_equal.yaml      # 2 STAs, 60s
uv run atf-run scenarios/00_smoke_test.yaml         # 1 STA, 30s (smoke test)
```

`atf-run` automatically:
1. Assigns a unique iperf3 port per STA (5201, 5202, 5203, …)
2. Spawns local `iperf3 -s` subprocess per port
3. Broadcasts `prepare` to all agents via MQTT
4. Waits for all `ack`s
5. Broadcasts `start_at` with a timestamp 5 seconds in the future
6. Each agent uses precision `sleep_until` (coarse sleep + 20ms busy-wait, < 1ms accuracy)
7. Streams per-second throughput to InfluxDB in real time
8. Collects results, writes `run_summary` to InfluxDB
9. Kills all iperf3 servers
10. Prints summary table

> No need to manually run `iperf3 -s` — the orchestrator handles everything.

### Sample output

```
✓  PASSED  run_id: run-01KQ60TE38N7XT9EQBDY36J9BS

  rpi-sta-01
    throughput : 134.2 Mbps avg  (±9.6)
    retransmits: 0
    sync_offset: 0 ms
  rpi-sta-02
    throughput : 142.0 Mbps avg  (±9.1)
    retransmits: 0
    sync_offset: 0 ms
```

### Writing custom scenarios

Scenarios are YAML files under `scenarios/`. Example:

```yaml
extends: _base/normal.yaml
name: "Three STA Asymmetric"
duration_sec: 60

preflight:
  expected_agents: ["rpi-sta-01", "rpi-sta-02", "rpi-sta-03"]

stations:
  - node: rpi-sta-01
    traffic:
      type: iperf3_tcp
      server: "atf-broker.local"
  - node: rpi-sta-02
    traffic:
      type: iperf3_tcp
      server: "atf-broker.local"
  - node: rpi-sta-03
    traffic:
      type: iperf3_udp
      server: "atf-broker.local"
      bandwidth_mbps: 50
```

Ports are auto-assigned by the orchestrator — do not specify them in the YAML.

---

## 8. Viewing Results

### Real-time Grafana dashboard

1. Open `http://localhost:3000` → Dashboards → **ATF Validator**
2. Set time range: **Last 5 minutes**
3. Set auto-refresh: **5s**
4. Run `atf-run` — lines appear in real-time

The dashboard has three panels:
- **Throughput per STA** (top): per-second time series, one line per STA per run
- **Sync Offset per STA** (bottom-left): bar chart showing start-time jitter (target: < 100 ms)
- **Live Avg Throughput per STA** (bottom-right): rolling average that updates every 5s during the test, persists after, switches when next test starts

### AP airtime collector (optional)

To capture per-station airtime usage from the AP's perspective (mt76 debugfs), run the AP collector in a separate terminal:

```bash
uv run atf-ap-collector --ap 192.168.1.1 --interval 1
```

It SSHes into the AP every second, reads `/sys/kernel/debug/ieee80211/phy1/netdev:phy1-ap0/stations/{MAC}/airtime`, computes RX/TX delta percentages, and writes to InfluxDB measurement `ap_airtime`. The MAC↔agent_id mapping is auto-built by subscribing to retained `atf/agent/+/status` messages — no config needed.

Grafana panel **AP Airtime per STA (TX %)** shows the resulting curves alongside throughput.

### Inspector (live agent status)

```bash
uv run atf-inspector  # http://localhost:8080
```

Shows online/offline state of each agent, NTP offset, current state machine state.

### Querying InfluxDB directly

```bash
TOKEN=$(curl -s -X POST http://localhost:8086/api/v2/signin -u admin:atf-admin-2026 \
  --cookie-jar /tmp/c >/dev/null && \
  curl -s --cookie /tmp/c http://localhost:8086/api/v2/authorizations | \
  python3 -c "import sys,json; print([a['token'] for a in json.load(sys.stdin)['authorizations'] if a['status']=='active'][0])")

curl -s -X POST "http://localhost:8086/api/v2/query?org=atf" \
  -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/vnd.flux" \
  --data 'from(bucket:"atf_metrics") |> range(start: -1h) |> filter(fn:(r) => r._measurement == "run_summary") |> last()'
```

---

## 9. Re-deploying Code

After modifying agent code on Mac, push to all RPis:

```bash
for ip in 192.168.1.221 192.168.1.233; do
  rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    -e "ssh -i ~/.ssh/id_ed25519_personal" \
    ~/workspace/syncbench/ mars@$ip:~/syncbench/
  ssh -i ~/.ssh/id_ed25519_personal mars@$ip "sudo systemctl restart atf-agent"
done
```

Controller-side changes (orchestrator, scenarios, dashboard) take effect immediately on the next `atf-run`.

For Grafana dashboard JSON changes:
```bash
curl -s -X POST -u admin:atf-grafana-2026 http://localhost:3000/api/admin/provisioning/dashboards/reload
```

---

## 10. Troubleshooting

### RPi connects to wrong Wi-Fi

Symptom: RPi gets a non-`192.168.1.x` IP.

```bash
ssh mars@<current_ip>
sudo nmcli dev wifi connect atf_test_5g password 12345678
# OR edit /etc/wpa_supplicant/wpa_supplicant.conf
```

### Ubuntu / Debian: `setup-linux.sh` fails with "sudo: a password is required"

Ubuntu/Debian users do NOT have NOPASSWD sudo by default (RPi imager presets it). On the target device, run once:

```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER-nopasswd
sudo chmod 0440 /etc/sudoers.d/$USER-nopasswd
```

Then re-run `setup-linux.sh` from the controller.

### `atf-agent` fails to start with `status=217/USER`

Symptom: `systemctl status atf-agent` shows `code=exited, status=217/USER`.

The systemd service has a wrong `User=` setting. Fix:
```bash
sudo sed -i 's/User=pi/User=mars/' /etc/systemd/system/atf-agent.service
sudo systemctl daemon-reload
sudo systemctl restart atf-agent
```

> `setup-linux.sh` now sets `User=$(whoami)` automatically — only old deployments hit this.

### `atf-run` fails with "iperf3 timeout"

Symptom: orchestrator logs show "Result received from <agent>" never appears.

Check agent log:
```bash
ssh mars@<rpi_ip> "journalctl -u atf-agent -n 30"
```

Common causes:
- iperf3 server unreachable from RPi (firewall, wrong subnet)
- iperf3 version mismatch in JSON parsing — fixed by current text-mode streaming
- Agent thread crashed — see traceback in journalctl

### Grafana shows no data

1. Verify InfluxDB has data:
   ```bash
   curl -s -u admin:atf-admin-2026 http://localhost:8086/health
   ```
2. Verify Grafana datasource is provisioned:
   ```bash
   curl -s -u admin:atf-grafana-2026 http://localhost:3000/api/datasources
   ```
   Should show `atf-influxdb`.
3. Check time range covers when the test ran (default: last 15 min)
4. Force dashboard reload:
   ```bash
   curl -s -X POST -u admin:atf-grafana-2026 http://localhost:3000/api/admin/provisioning/dashboards/reload
   ```

### Sync offset > 100 ms

- Check NTP sync on each RPi: `chronyc tracking | grep "System time"`
- Ensure RPis are all on the same NTP server (default: pool.ntp.org)
- For lab use, run a local NTP server on the Mac mini for tighter sync

### Multiple RPis show `raspberrypi.local`

Symptom: Two RPis broadcast the same mDNS name, mDNS resolution becomes random.

Fix: re-run `setup-linux.sh --agent-id rpi-sta-XX` on each RPi (it sets a unique hostname). Or manually:
```bash
sudo hostnamectl set-hostname rpi-sta-02
sudo sed -i 's/raspberrypi/rpi-sta-02/g' /etc/hosts
sudo reboot
```

---

## License

Apache 2.0 — see [LICENSE](../LICENSE)
