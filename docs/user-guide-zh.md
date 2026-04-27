# ATF Validator — 使用者指南

ATF（Airtime Fairness）驗證框架的完整安裝、部署、操作指南，適用於 1 個 AP + N 台 STA 的 Wi-Fi 測試環境。

> English version: [user-guide-en.md](user-guide-en.md)

---

## 目錄

1. [這是什麼？](#1-這是什麼)
2. [架構與拓撲](#2-架構與拓撲)
3. [硬體需求](#3-硬體需求)
4. [Mac mini 設定（Controller）](#4-mac-mini-設定controller)
5. [AX4200 設定（AP）](#5-ax4200-設定ap)
6. [Raspberry Pi 設定（STA）](#6-raspberry-pi-設定sta)
7. [執行測試](#7-執行測試)
8. [檢視結果](#8-檢視結果)
9. [重新部署程式碼](#9-重新部署程式碼)
10. [疑難排解](#10-疑難排解)

---

## 1. 這是什麼？

ATF Validator 是一個 **平台無關（platform-agnostic）** 的 Wi-Fi Airtime Fairness 驗證框架，用於消費級 Wi-Fi 硬體上的 IEEE 802.11 ATF 行為驗證。它協調多台 Raspberry Pi 同步對單一 AP 跑 iperf3，量測每台 STA 的吞吐量與起跑時間誤差，並在 Grafana 即時可視化。

**Phase 1 目標：**
- 一條指令執行：`atf-run scenarios/01_two_sta_equal.yaml`
- Grafana 即時顯示每台 STA 吞吐量曲線
- 同步精度 <100 ms（實測 0–1 ms）
- 系統重啟後 5 分鐘內可重新跑測試

框架本身不綁 Wi-Fi — scenario 裡 ATF on/off 的部分才是 Wi-Fi 專屬。同樣的 orchestration pipeline 可用於有線 QoS 測試、混合協定流量、任何需要多端點同步的測試場景。

---

## 2. 架構與拓撲

### 網路拓撲

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

- **Mac mini**：跑 controller、MQTT broker、InfluxDB、Grafana、iperf3 servers
- **AX4200**：受測裝置（要量測 ATF 行為的 AP）
- **Raspberry Pi**：以 Wi-Fi 連線並產生 iperf3 流量的 STA

### 軟體棧

| 層級 | 元件 | 執行位置 |
|---|---|---|
| 測試場景 | YAML 檔案 | Mac mini |
| Orchestrator | `atf-run` CLI | Mac mini |
| Inspector 儀表板 | FastAPI + SSE | Mac mini（`http://localhost:8080`）|
| 指標儀表板 | Grafana 11 | Mac mini（`http://localhost:3000`）|
| 時間序列資料庫 | InfluxDB 2.7 | Mac mini |
| 訊息匯流排 | Mosquitto MQTT 2.0 | Mac mini |
| Agent | `atf-agent` systemd 服務 | 每台 RPi |
| 流量工具 | `iperf3` server（Mac）/ client（RPi）| 兩端 |

### 資料流

```
RPi iperf3 client ──每秒一個 sample──▶ MQTT live topic
                                            │
Mac mini orchestrator ──訂閱─────────────────┘
        │
        ├─▶ InfluxDB（即時寫入）
        │       │
        │       ▼
        │   Grafana（5 秒刷新，曲線即時繪製）
        │
        └─▶ run_summary（測試結束後寫）
```

---

## 3. 硬體需求

| 項目 | 數量 | 規格 |
|---|---|---|
| Mac mini（Apple Silicon）| 1 | macOS、≥8GB RAM、Ethernet port |
| OpenWrt AP | 1 | ASUS AX4200（MT7986A，mt76 driver）— 支援 `AIRTIME_FAIRNESS` + `AQL` |
| Raspberry Pi | 2–5 | RPi 4 / 400 / 500，5GHz Wi-Fi（Wi-Fi 6 最佳）|
| microSD 卡 | 每台 RPi 一張 | ≥16 GB，Class 10 以上 |
| Ethernet 線 | 1 條 | Mac mini ↔ AX4200 LAN port |
| 電源 | 每台一個 | RPi 用 USB-C，AX4200 用圓孔 DC |

> 必須用 5 GHz（2.4 GHz 雜訊太多，量測不可重複）。確認 AP 晶片組在 mac80211 支援 ATF（mt76、ath9k、ath10k、ath11k 都支援）。

---

## 4. Mac mini 設定（Controller）

### 4.1 安裝必要工具

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc

# 工具
brew install uv mosquitto gnupg pinentry-mac iperf3
brew install --cask docker
uv python install 3.11
```

打開 **Docker.app**，等到選單列圖示顯示「Docker Desktop is running」。

### 4.2 SSH + GPG 金鑰（GitHub 用）

```bash
ssh-keygen -t ed25519 -C "your@email.com" -f ~/.ssh/id_ed25519_personal
gpg --full-generate-key  # RSA 4096，不過期
```

把 public keys 上傳到 GitHub → Settings → SSH Keys / GPG Keys。

### 4.3 設定 mDNS hostname（broker 自動發現）

```bash
sudo scutil --set LocalHostName atf-broker
ping atf-broker.local  # 應該要通
```

這樣 LAN 上任何裝置都能用 `atf-broker.local` 找到 Mac mini。RPi 用這個 hostname 找 MQTT broker — 不需要寫死 IP。

### 4.4 Clone 並安裝

```bash
git clone git@github.com:marsyanggo/atf-validator.git
cd atf-validator
uv sync
```

### 4.5 啟動基礎服務

```bash
docker compose up -d
```

驗證：
```bash
curl localhost:8086/health           # InfluxDB → {"status":"pass",...}
docker compose ps                    # 三個服務都是 Up
```

> Grafana datasource 與 ATF Validator dashboard 第一次啟動時自動 provision，不需要手動設定。

打開 `http://localhost:3000`（admin / atf-grafana-2026）確認 Grafana 上線。

---

## 5. AX4200 設定（AP）

> 一次性設定。已經做過可以跳過。

### 5.1 用 Ethernet 把 Mac mini 接到 AX4200 的 LAN port

確認 Mac mini 的 Ethernet IP 在 `192.168.1.x` 範圍：
```bash
ifconfig en0 | grep inet
```

### 5.2 SSH 進去打開 5 GHz Wi-Fi

```bash
ssh root@192.168.1.1

uci set wireless.radio1.disabled='0'
uci set wireless.default_radio1.ssid='atf_test_5g'
uci set wireless.default_radio1.encryption='psk2+ccmp'
uci set wireless.default_radio1.key='12345678'
uci commit wireless
wifi up
```

> `radio1` = 5 GHz（MT7986A）。`radio0`（2.4 GHz）保持 disabled，避免干擾。

### 5.3 確認 ATF 支援

```bash
ssh root@192.168.1.1 "iw phy phy1 info | grep -i airtime"
# 預期：AIRTIME_FAIRNESS, AQL
```

### 5.4 開關 ATF（Week 4 ATF on/off 對比測試用）

```bash
# ATF on（dynamic mode）
ssh root@192.168.1.1 "uci set wireless.radio1.airtime_mode='2' && uci commit && wifi reload"

# ATF off
ssh root@192.168.1.1 "uci set wireless.radio1.airtime_mode='0' && uci commit && wifi reload"
```

---

## 6. Raspberry Pi 設定（STA）

每台 RPi 都做一次。每台給不同的 `--agent-id`（`rpi-sta-01`、`rpi-sta-02`…）。

### 6.1 燒 Raspberry Pi OS Lite（64-bit, Bookworm 或更新）

用 **Raspberry Pi Imager**：
- OS：Raspberry Pi OS Lite (64-bit)
- 燒錄時設定 username/password（例如 `mars` / `<你的密碼>`）
- 啟用 SSH server
- 設定 Wi-Fi：SSID `atf_test_5g`，密碼 `12345678`

> 如果燒錄時沒設 Wi-Fi，先把 RPi 連任意 Wi-Fi，SSH 進去後用 `nmcli` 或 `wpa_supplicant` 切到 `atf_test_5g`。

### 6.2 找出 RPi 的 IP

開機後 RPi 會加入 AX4200 網路。在 Mac mini：
```bash
ping raspberrypi.local
# 或查 AX4200 DHCP leases：
ssh root@192.168.1.1 "cat /tmp/dhcp.leases"
```

### 6.3 複製 SSH key（每台 RPi 一次）

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_personal.pub mars@raspberrypi.local
# 輸入燒錄時設定的密碼
```

### 6.4 部署並執行 setup script

```bash
RPI_IP=192.168.1.221      # 步驟 6.2 找到的 IP
AGENT_ID=rpi-sta-01       # 每台 RPi 不同

# 1. Rsync 程式碼
rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  -e "ssh -i ~/.ssh/id_ed25519_personal" \
  ~/workspace/atf-validator/ mars@$RPI_IP:~/atf-validator/

# 2. 跑 setup
ssh -i ~/.ssh/id_ed25519_personal mars@$RPI_IP \
  "bash ~/atf-validator/scripts/setup-rpi.sh --broker atf-broker.local --agent-id $AGENT_ID"
```

`setup-rpi.sh` 會自動：
1. 把 hostname 設成 `$AGENT_ID` → 重開機後可以用 `<agent-id>.local` 連
2. 透過 apt 安裝 `iperf3`、`iw`、`chrony`
3. 安裝 `uv` + Python 3.11
4. 跑 `uv sync`
5. 安裝並啟用 `atf-agent` systemd 服務

### 6.5 啟動 agent

```bash
ssh -i ~/.ssh/id_ed25519_personal mars@$RPI_IP "sudo systemctl start atf-agent"

# 看 log
ssh -i ~/.ssh/id_ed25519_personal mars@$RPI_IP "journalctl -u atf-agent -f"
# 預期：State → BOOT → IDLE，NTP synced
```

### 6.6 在 Mac mini 用 Inspector 確認

```bash
uv run atf-inspector
# 開 http://localhost:8080
# 應該顯示：● online  rpi-sta-01  IDLE  +0.0ms
```

---

## 7. 執行測試

### 一條指令搞定

```bash
uv run atf-run scenarios/01_two_sta_equal.yaml      # 2 STA，60 秒
uv run atf-run scenarios/00_smoke_test.yaml         # 1 STA，30 秒（smoke test）
```

`atf-run` 會自動：
1. 給每台 STA 分配獨立的 iperf3 port（5201、5202、5203…）
2. 在本機為每個 port spawn `iperf3 -s` subprocess
3. MQTT broadcast `prepare` 給所有 agent
4. 等所有 agent 回 `ack`
5. Broadcast `start_at`，附上 5 秒後的 timestamp
6. 每個 agent 用精準 `sleep_until`（粗略 sleep + 20ms busy-wait，<1ms 精度）
7. 測試中每秒把 throughput 即時串流到 InfluxDB
8. 收齊 result，把 `run_summary` 寫進 InfluxDB
9. 關掉所有 iperf3 server
10. 印出摘要表格

> **不需要手動跑 `iperf3 -s`**，orchestrator 全部自動處理。

### 範例輸出

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

### 自訂 scenario

Scenario 是 `scenarios/` 下的 YAML 檔案。範例：

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

Port 由 orchestrator 自動分配，**不要在 YAML 裡指定**。

---

## 8. 檢視結果

### Grafana 即時儀表板

1. 開 `http://localhost:3000` → Dashboards → **ATF Validator**
2. 時間範圍：**Last 5 minutes**
3. Auto-refresh：**5s**
4. 跑 `atf-run` — 線會即時畫出來

儀表板有三個 panel：
- **Throughput per STA**（上）：每秒時間序列，每個 STA × 每個 run 一條線
- **Sync Offset per STA**（左下）：bar chart 顯示起跑誤差（目標 < 100 ms）
- **Live Avg Throughput per STA**（右下）：rolling average，測試中每 5 秒更新，結束後保留，下次測試自動切換

### AP airtime collector（選用）

要從 AP 視角記錄每台 STA 的 airtime 使用率（mt76 debugfs），在另一個 terminal 跑 AP collector：

```bash
uv run atf-ap-collector --ap 192.168.1.1 --interval 1
```

每秒 SSH 進 AP 讀 `/sys/kernel/debug/ieee80211/phy1/netdev:phy1-ap0/stations/{MAC}/airtime`，算 RX/TX delta 百分比，寫進 InfluxDB measurement `ap_airtime`。MAC↔agent_id 對應透過訂閱 retained `atf/agent/+/status` 自動建立，不需要設定檔。

Grafana panel **AP Airtime per STA (TX %)** 顯示對應曲線，與 throughput 對照查看。

### Inspector（即時 agent 狀態）

```bash
uv run atf-inspector  # http://localhost:8080
```

顯示每台 agent 的線上/離線狀態、NTP offset、目前狀態機狀態。

### 直接查 InfluxDB

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

## 9. 重新部署程式碼

修改 Mac 上的 agent 程式碼後，推送到所有 RPi：

```bash
for ip in 192.168.1.221 192.168.1.233; do
  rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    -e "ssh -i ~/.ssh/id_ed25519_personal" \
    ~/workspace/atf-validator/ mars@$ip:~/atf-validator/
  ssh -i ~/.ssh/id_ed25519_personal mars@$ip "sudo systemctl restart atf-agent"
done
```

Controller 端的改動（orchestrator、scenario、dashboard）下次 `atf-run` 立即生效。

Grafana dashboard JSON 改完後：
```bash
curl -s -X POST -u admin:atf-grafana-2026 http://localhost:3000/api/admin/provisioning/dashboards/reload
```

---

## 10. 疑難排解

### RPi 連到錯的 Wi-Fi

症狀：RPi 拿到非 `192.168.1.x` 的 IP。

```bash
ssh mars@<目前的 IP>
sudo nmcli dev wifi connect atf_test_5g password 12345678
# 或編輯 /etc/wpa_supplicant/wpa_supplicant.conf
```

### `atf-agent` 啟動失敗 `status=217/USER`

症狀：`systemctl status atf-agent` 顯示 `code=exited, status=217/USER`。

systemd service 的 `User=` 設錯了。修正：
```bash
sudo sed -i 's/User=pi/User=mars/' /etc/systemd/system/atf-agent.service
sudo systemctl daemon-reload
sudo systemctl restart atf-agent
```

> `setup-rpi.sh` 現在會自動設 `User=$(whoami)`，只有舊部署會踩到。

### `atf-run` 失敗 "iperf3 timeout"

症狀：orchestrator log 沒出現 "Result received from <agent>"。

看 agent log：
```bash
ssh mars@<rpi_ip> "journalctl -u atf-agent -n 30"
```

常見原因：
- iperf3 server 從 RPi 連不到（防火牆、網段不對）
- iperf3 版本不一致導致 JSON parsing 失敗 — 目前的 text-mode streaming 已修復
- Agent thread crash — 看 journalctl 的 traceback

### Grafana 顯示 no data

1. 確認 InfluxDB 有資料：
   ```bash
   curl -s -u admin:atf-admin-2026 http://localhost:8086/health
   ```
2. 確認 Grafana datasource 有 provision：
   ```bash
   curl -s -u admin:atf-grafana-2026 http://localhost:3000/api/datasources
   ```
   應該看到 `atf-influxdb`。
3. 檢查時間範圍涵蓋測試執行的時段（預設 last 15 min）
4. 強制 reload dashboard：
   ```bash
   curl -s -X POST -u admin:atf-grafana-2026 http://localhost:3000/api/admin/provisioning/dashboards/reload
   ```

### Sync offset > 100 ms

- 檢查每台 RPi 的 NTP 同步：`chronyc tracking | grep "System time"`
- 確保所有 RPi 都用同一個 NTP server（預設 pool.ntp.org）
- 實驗室用途可以在 Mac mini 跑 local NTP server 提高精度

### 多台 RPi 都叫 `raspberrypi.local`

症狀：兩台 RPi 廣播同樣的 mDNS 名字，解析變成隨機。

修正：每台 RPi 重跑 `setup-rpi.sh --agent-id rpi-sta-XX`（會自動設唯一 hostname）。或手動：
```bash
sudo hostnamectl set-hostname rpi-sta-02
sudo sed -i 's/raspberrypi/rpi-sta-02/g' /etc/hosts
sudo reboot
```

---

## License

Apache 2.0 — see [LICENSE](../LICENSE)
