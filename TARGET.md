# Project Targets — ATF Validator

_Last updated: 2026-04-26 00:40 PDT_

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
