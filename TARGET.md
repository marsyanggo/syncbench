# Project Targets — syncbench

_Last updated: 2026-04-29_

---

## Goal: Phase 1 — 架構驗證 (1 AP + 3–5 STA)

> 在 5 台 STA 規模下跑通整個驗證框架，達成 Phase 1 成功定義的 5 個條件。

### Phase 1 成功條件 (全部達成才算完成)

- [x] 單指令執行：`atf-run` 觸發多台 STA 同步啟動 iperf3 ✅
- [x] 即時可視化：Grafana 顯示每台 STA 吞吐量曲線 ✅
- [x] 自動報告：測試結束後自動產出 markdown 報告 + Jain's FI ✅
- [x] 同步精度量化：STA 起跑時間誤差實測 0 ms ✅
- [x] 可重現性：`docker compose up -d` + `atf-run` 即可重現 ✅

---

## Goal: Week 1 — 基礎設施 + 單台 RPi 跑通 ✅

---

## Goal: Week 2 — 單 STA 測試端到端 ✅

---

## Goal: Week 3 — 多台 STA + 同步驗證 ✅

---

## Goal: Week 4 — 報告產出 + 文件 + 規模到 6 台

- [x] `reporter.py` + `fairness.py`：Jain's FI 自動計算
- [x] `docs/architecture.md`、`methodology.md`
- [x] `CONTRIBUTING.md`、`SECURITY.md`、`NOTICE`
- [x] rpi-sta-03/04/05 setup（RPi 5 × 2 + RPi 4 × 1）
- [x] `scenarios/03_five_sta_rpi.yaml`：5 台 RPi 同步跑通（JFI = 0.886）
- [x] `scenarios/04_six_sta_mixed.yaml`：5 RPi + 1 NB 跑通（JFI = 0.521）
- [x] ATF on/off 對比跑通（AX4200 HE80 下 ATF 無效，記錄於 methodology.md）
- [x] cli.py 自動載入 `.env`（不需手動 export INFLUXDB_TOKEN）
- [x] orchestrator.py InfluxDB write error catch（防止 MQTT 連線 crash）
- [x] README 更新：6-STA demo 影片 + reference results 表格
- [ ] repo 轉 public（待律師確認）

---

## Goal: Phase 2 — Integrated Web UI (Inspector + Native Chart)

> 把 CLI workflow 變成單一網頁：在線 device 選取 → 一鍵觸發 run → 即時圖表呈現結果，移除 Grafana 依賴。

### Step 1 — Orchestrator 改成 callable

- [ ] 把 `Orchestrator` 從 CLI-only 拆成 importable class（`async run(scenario_dict) → RunResult`）
- [ ] `atf-run` CLI 改成薄包裝層，呼叫同一個 class（確保 CLI 繼續可用）

### Step 2 — Inspector Run API

- [ ] `POST /api/run`：接受 `{agents: [...], duration: int}`，動態產生 scenario，呼叫 Orchestrator
- [ ] `GET /api/run/{run_id}/stream` SSE：即時推送 run 進度（prepare / running / done + 結果）
- [ ] `GET /api/metrics/live/{run_id}`：從 MQTT live topic 或 InfluxDB 取每秒 throughput，回傳 JSON time-series

### Step 3 — Inspector UI 重設計

- [ ] 頁面 layout 重構：左欄 agent 選取、中間 run 控制 + 結果、右欄即時圖表
- [ ] Agent 列表加 checkbox（online 才可選）+ duration input + Start Run 按鈕
- [ ] Run 進度顯示（phase badge：PREPARING → RUNNING → DONE）
- [ ] 跑完後結果表格：throughput avg / stdev / retransmits / sync_offset / JFI

### Step 4 — Native Chart（Chart.js）

- [ ] 引入 Chart.js（CDN，不需 build step）
- [ ] SSE-driven 即時曲線：測試進行中每秒 append 新資料點，各 agent 一條線
- [ ] 跑完後曲線定格，顯示完整 60 秒數據
- [ ] Hover agent 列表時對應曲線高亮

### Step 5 — 移除 Grafana 依賴（選做）

- [ ] `docker-compose.yml` 把 Grafana 標為 optional profile
- [ ] `docs/` 更新：說明 Inspector 已內建圖表，Grafana 為進階選項

---

## Goal: 法律合規準備

- [x] 私人設備/時間確認、git identity、GPG、GitHub repo
- [x] 開發日誌 WORKLOG.md
- [ ] 諮詢加州執業智財/僱傭律師（California Labor Code §2870）
