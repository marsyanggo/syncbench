# Project Targets — ATF Validator

_Last updated: 2026-04-25 23:16 PDT_

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
- [ ] Step 0-c：建立 `pyproject.toml`（paho-mqtt, fastapi, uvicorn, pydantic, influxdb-client）

- [ ] Step 1：建立 `docker-compose.yml`（Mosquitto port 1883、InfluxDB port 8086、Grafana port 3000）
- [ ] Step 1：`docker compose up -d` → 確認三服務互通（`curl localhost:8086/health` 回 OK）
- [ ] Step 1：Mosquitto 套用 spec §5.8 設定（allow_anonymous、persistence、log）
- [ ] Step 1：InfluxDB 建立 bucket `atf_metrics`，Grafana datasource 指向 InfluxDB

- [ ] Step 2：建立 `controller/atf_ctrl/mqtt_bus.py`（`MQTTBus` class）
- [ ] Step 2：實作 `connect()`（含 LWT 參數）、`publish()`（自動注入 envelope: v/ts/msg_id）、`subscribe()`、`loop_forever()`
- [ ] Step 2：smoke test：一個 publish + subscribe roundtrip 驗證通過

- [ ] Step 3：建立 `agent/atf_agent/main.py` 狀態機（BOOT → IDLE）
- [ ] Step 3：MQTT 連線 + LWT 設定（agent/{id}/status = OFFLINE）
- [ ] Step 3：Heartbeat publisher（1Hz、QoS 0）payload 含 `ntp_offset_ms`（chronyc tracking 解析）
- [ ] Step 3：訂閱 `atf/ctrl/broadcast/+` 和 `atf/ctrl/unicast/agent/{my_id}/+`
- [ ] Step 3：RPi 上跑起 agent，MQTT broker 能收到 heartbeat

- [ ] Step 4：建立 `controller/atf_ctrl/inspector/server.py`（FastAPI app）
- [ ] Step 4：`InspectorState` — `dict[agent_id → AgentState]`（記憶體 dict）
- [ ] Step 4：MQTT subscriber 訂閱 `agent/+/heartbeat`、`agent/+/status` → 更新 InspectorState
- [ ] Step 4：`GET /` → Jinja2 HTML agent 狀態表格
- [ ] Step 4：`GET /events` → SSE stream（state 變動即時推送）

- [ ] Step 5（里程碑）：瀏覽器開 localhost:8080 顯示 `rpi-sta-01 ●online NTP offset: 2.3ms state: IDLE`

---

## Goal: Week 2 — 單 STA 測試端到端

> `atf-run 00_smoke_test.yaml` 一條指令能跑完整流程

### Sub-tasks

- [ ] 寫 `iperf3.py` runner（JSON output 解析 + 1Hz throughput 回報）
- [ ] OpenWrt AP 確認型號並設好 hostapd 配置
- [ ] 寫 `scenarios/loader.py`，能 load YAML、validate schema
- [ ] Controller 實作 prepare / start_at / stop 最小流程
- [ ] Agent 實作 capability collector（L1 連接層 + L4 軟體層先做）→ Inspector 顯示
- [ ] 跑 `00_smoke_test.yaml`：1 台 STA 連 AP，跑 30 秒 iperf3 完整流程

---

## Goal: Week 3 — 3 台 STA + 同步驗證

> 3 台 STA 同步 <100 ms 已量化證明

### Sub-tasks

- [ ] 複製 RPi image 到 3 台，共 3 台 STA 能同時上線
- [ ] 跑 `01_two_sta_equal.yaml` 與 `02_three_sta_equal.yaml`
- [ ] 寫 `sync.py` 的 NTP-aware `sleep_until`，量測實際 sync offset
- [ ] Grafana 加 sync-quality dashboard（顯示所有 agent sync offset 分布）
- [ ] AP collector 接上，讀 debugfs 寫 InfluxDB
- [ ] 補完 capability collector（L2 PHY 層 + L3 QoS 層），Inspector 完整顯示四層
- [ ] SQLite store 接上 preflight，history sidebar 功能完成

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
