# Project Targets — ATF Validator

_Last updated: 2026-04-26 16:05 PDT_

---

## Goal: Phase 1 — 架構驗證(1 AP + 3–5 STA)

> 在 5 台 STA 規模下跑通整個驗證框架，達成 Phase 1 成功定義的 5 個條件。

### Phase 1 成功條件(全部達成才算完成)

- [ ] 單指令執行：`atf-run scenarios/01_two_sta_equal.yaml` 觸發 3–5 台 RPi 同步啟動 iperf3
- [ ] 即時可視化：Grafana 顯示每台 STA 吞吐量曲線
- [ ] 自動報告：測試結束後自動產出 markdown 報告（Jain's Fairness Index、各 STA 平均吞吐、retry 率）
- [ ] 同步精度量化：STA 起跑時間誤差實測 <100 ms（`sync_offset_ms` 欄位記錄）
- [ ] 可重現性：系統重啟後 5 分鐘內能再次跑同樣測試

---

## Goal: Week 1 — 基礎設施 + 單台 RPi 跑通

> Mac 上能看到「rpi-sta-01 online, NTP offset 2.3 ms」

### Sub-tasks

- [x] Step 0-a：git init + repo 骨架建立（LICENSE、README、WORKLOG、design_spec）
- [x] Step 0-b：push 到 GitHub private repo，設定 remote origin
- [x] Step 0-c：建立 `pyproject.toml`（paho-mqtt, fastapi, uvicorn, pydantic, influxdb-client）+ uv sync 通過

- [x] Step 1：建立 `docker-compose.yml`（Mosquitto port 1883、InfluxDB port 8086、Grafana port 3000）
- [x] Step 1：`docker compose up -d` → 確認三服務互通（`curl localhost:8086/health` 回 OK）
- [x] Step 1：Mosquitto 套用 spec §5.8 設定（allow_anonymous、persistence、log）
- [x] Step 1：InfluxDB 建立 bucket `atf_metrics`，Grafana datasource 指向 InfluxDB

- [x] Step 2：建立 `shared/mqtt_bus.py`（`MQTTBus` class，從 controller 抽出共用）
- [x] Step 2：實作 `connect()`（含 LWT）、`publish()`（自動注入 envelope: v/ts/msg_id）、`subscribe()`、`loop_forever()`
- [x] Step 2：smoke test 3 項通過（roundtrip、LWT、wildcard）

- [x] Step 3：建立 `agent/atf_agent/main.py` 狀態機（BOOT → IDLE）
- [x] Step 3：`PlatformAdapter` ABC + `LinuxAdapter`（RPi）+ `MacOSAdapter`（本機測試）
- [x] Step 3：MQTT 連線 + LWT 設定（agent/{id}/status = OFFLINE）
- [x] Step 3：Heartbeat publisher（1Hz、QoS 0）payload 含 `ntp_offset_ms`
- [x] Step 3：訂閱 `atf/ctrl/broadcast/+` 和 `atf/ctrl/unicast/agent/{my_id}/+`
- [x] Step 3：Mac 本機跑 agent 驗證通過（BOOT→IDLE，RPi 待實機確認）
- [x] Step 3：`deploy/rpi-image/Dockerfile`（ARM64 多架構，含 iw/chrony/iperf3）

- [x] Step 4：建立 `controller/atf_ctrl/inspector/server.py`（FastAPI + lifespan）
- [x] Step 4：`InspectorState`（thread-safe，heartbeat + status 更新）
- [x] Step 4：MQTT subscriber 訂閱 `agent/+/heartbeat`、`agent/+/status`
- [x] Step 4：`GET /` → 暗色主題 HTML 儀表板
- [x] Step 4：`GET /events` → SSE stream（EventSource 即時推送）

- [x] Step 5（里程碑）：瀏覽器 localhost:8080 顯示 `rpi-sta-01 ●online +0.0ms IDLE` ✅

---

## Goal: Week 2 — 單 STA 測試端到端

> `atf-run 00_smoke_test.yaml` 一條指令能跑完整流程（ATF 先不 enable，專注跑通 pipeline）

### 硬體線（需要 RPi）

- [x] A1：燒 RPi OS Lite 64-bit（bookworm）— 已完成
- [x] A2：RPi 連上 AX4200 Wi-Fi（atf_test_5g），SSH 可進（rpi-sta-01 IP: 192.168.1.221）
- [x] A3：RPi 安裝 iperf3 + chrony + iw + uv + Python 3.11
- [x] A4：clone repo + `uv sync`，部署 agent 程式碼
- [x] A5：`atf-agent` 跑起來，Inspector 顯示 rpi-sta-01 IDLE

### 軟體線（Mac，可立即開始）

- [x] B1：`agent/atf_agent/traffic/iperf3.py` — 包裝 iperf3 CLI，解析 JSON output，回傳 ThroughputSample + 統計
- [x] B1：5 項測試通過（TCP roundtrip、timestamp、sequential、unreachable、UDP）

- [x] B2：`controller/atf_ctrl/scenarios/models.py` — Pydantic models（Scenario, StationConfig, TrafficConfig, PreflightConfig）
- [x] B3：`controller/atf_ctrl/scenarios/loader.py` — `load(yaml_path)` + extends deep merge
- [x] B3：建立 `scenarios/_base/normal.yaml` + `scenarios/00_smoke_test.yaml` + `01_two_sta_equal.yaml`
- [x] B3：5 項測試通過（load、extends merge、multi-STA、missing file、invalid YAML）

- [x] B4：`controller/atf_ctrl/orchestrator.py` — prepare → 等 ack → start_at → 等 duration → stop → collect results
- [x] B4：agent 完整跑 iperf3 並回傳 result，orchestrator 收到 ok=True

- [x] B5：`controller/atf_ctrl/cli.py` — `atf-run` + `atf-status` CLI entry point
- [x] B5：`scripts/local-test.sh` + `scenarios/00_smoke_test_local.yaml`（5/5 checks pass）

### 合體驗收

- [x] OpenWrt AP 確認型號：ASUS AX4200（MT7986A Filogic 830，mt76 driver）
- [x] AX4200 5GHz Wi-Fi 設定（SSID: atf_test_5g, WPA2 AES, ch36 HE80）
- [x] Mac mini mDNS hostname 設定（`atf-broker.local`，`sudo scutil --set LocalHostName atf-broker`）
- [x] `scenarios/*.yaml` broker IP 全改為 `atf-broker.local`（支援 DHCP 環境彈性）
- [x] `scripts/setup-rpi.sh` 更新（Step 0 自動設 hostname、`User=$(whoami)` 動態抓）
- [x] `docs/development-setup.md` 新增 Hardware Network Setup 段落
- [x] hostapd 確認 ATF 介面存在（`iw phy phy1 info | grep airtime` → AIRTIME_FAIRNESS + AQL）
- [x] Mac mini 跑 `iperf3-darwin -s`，RPi 作為 iperf3 client 連 AP（252 Mbps）
- [x] `atf-run scenarios/00_smoke_test.yaml` 一條指令跑完整流程（30 秒）PASSED
- [x] Inspector 顯示 rpi-sta-01 state: RUNNING → REPORTING → IDLE
- [x] `iperf3.py` bug fix：`timemillisecs` KeyError → `timesecs * 1000` fallback（iperf3 3.18 相容）

---

## Goal: Week 3 — 多台 STA + 同步驗證

> 2 台先跑通，架構驗證後彈性擴充到 3 台。同步誤差 <100ms 量化證明。

### Step 1 — 第 2 台 RPi 上線

- [x] rpi-sta-02：`setup-rpi.sh --agent-id rpi-sta-02`（IP: 192.168.1.233）
- [x] Inspector 同時顯示 rpi-sta-01 + rpi-sta-02 ●online

### Step 2 — 2 台同步測試

- [x] Orchestrator 自動管理 iperf3 server（port pool 5201/5202，subprocess spawn/kill，N 台自動擴充）
- [x] `01_two_sta_equal.yaml` 移除 hardcoded port，由 orchestrator 動態分配
- [x] 跑 `atf-run scenarios/01_two_sta_equal.yaml`（2 台同時 iperf3）PASSED
- [x] sync_offset 實測 0–1ms（< 100ms 門檻）✅

### Step 3 — sync 精度提升

- [x] `shared/sync.py` NTP-aware `sleep_until`（coarse sleep + 20ms busy-wait hybrid）
- [x] sync_offset 實測穩定 0ms，每次 run 記錄在 result payload

### Step 4 — Grafana dashboard

- [x] `controller/atf_ctrl/metrics/influx_writer.py`：per-interval samples + run_summary 寫 InfluxDB
- [x] Agent result payload 加入 `samples` 陣列（每秒一點）
- [x] Grafana dashboard（throughput 曲線 / sync offset bar / mean stat 三 panels）
- [x] Grafana datasource provisioning + docker-compose datasources volume mount
- [x] 驗證：122 points 寫入成功，`http://localhost:3000` 看到圖

### Step 5 — AP collector

- [ ] SSH into AX4200，讀 `/sys/kernel/debug/ieee80211/phy1/mt76/airtime`
- [ ] AP collector 定期寫 InfluxDB（airtime per station）

### Step 6 — 擴充到 3 台（選做，有第 3 台 RPi 再做）

- [ ] 燒第 3 台 RPi，`--agent-id rpi-sta-03`
- [ ] 建立 `02_three_sta_equal.yaml`，跑通 3 台同步

---

## Goal: Week 4 — 報告產出 + 文件 + 規模到 5 台

> Phase 1 成功定義 5 條全部達成，專案可乾淨 push 到 GitHub

### Sub-tasks

- [ ] 寫 `reporter.py`：測完自動產 markdown + PNG 圖表
- [ ] 實作 Jain's Fairness Index 計算
- [ ] 跑 `03_asymmetric_rate.yaml`，驗證 ATF on/off 對 fairness 差異
- [ ] 補完 `docs/architecture.md` 與 `docs/methodology.md`
- [ ] 加到 5 台 STA，做最後規模壓測
- [ ] 加 Apache 2.0 LICENSE、NOTICE、CONTRIBUTING.md（DCO 規則）、SECURITY.md
- [ ] 確認 repo 無廠商私有資訊，可公開 push

---

## Goal: 法律合規準備

> 動工前完成，確保 IP 清晰

### Sub-tasks

- [ ] 諮詢加州執業智財/僱傭律師（California Labor Code §2870 風險評估）
- [x] 確認所有開發工作在私人時間、私人設備上完成（Mac mini 私人設備，週六晚上）
- [x] 建立開發日誌（時間戳記 + 使用設備記錄）
- [x] 安裝開發工具：Homebrew + gnupg + pinentry-mac
- [x] 設定 git identity（personal email + GPG signing key 55B37F93D54FF60A）
- [x] 產生 SSH key（id_ed25519_personal）+ 上傳至 GitHub
- [x] 產生 GPG key + 上傳至 GitHub（Verified commits）
- [x] 建立 GitHub personal private repo（marsyanggo/atf-validator）
- [x] Initial commit：GPG signed independence 宣告書（commit f50c101）
- [x] 建立 Apache 2.0 LICENSE（Copyright 2026 Mars Yang）
- [x] 建立 README.md（定位錨點：platform-agnostic ATF validation framework）
