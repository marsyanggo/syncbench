# Project Targets — syncbench

_Last updated: 2026-04-30_

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

- [x] `Orchestrator.run()` 加 `on_event` callback（phase / sample / done / error）
- [x] `atf-run` CLI 繼續可用（on_event=None 預設）

### Step 2 — Inspector Run API

- [x] `POST /api/run`：接受 `{agents, duration}`，動態產生 scenario，背景 thread 跑 Orchestrator
- [x] `GET /api/run/{run_id}/stream` SSE：推送 phase / sample / done / error 事件
- [x] `GET /api/metrics/{run_id}`：InfluxDB 歷史 time-series query

### Step 3 — Inspector UI 重設計

- [x] 3 欄 layout：左欄 device 選取、中欄 run 控制 + 結果、右欄即時圖表
- [x] Agent checkbox（online 才可選，running 中 disabled）+ duration input + Start Run
- [x] Run phase badge（IDLE / PREPARING / RUNNING / COLLECTING / DONE / ERROR）+ 進度條
- [x] 結果表格：throughput avg / stdev / retransmits / sync_offset + JFI

### Step 4 — Native Chart（Chart.js）

- [x] Chart.js CDN，不需 build step
- [x] SSE sample 事件驅動即時曲線，RPi ts_ms 為 x 軸基準（跨 agent 對齊）
- [x] spanGaps: true（無斷線）
- [x] Golden ratio hue 動態配色（100+ agent 不重複）
- [x] 左欄即時顯示每秒 throughput，中欄顯示 rolling avg

### Step 5 — 移除 Grafana 依賴（選做）

- [x] `docker-compose.yml` 把 Grafana 標為 optional profile（`--profile monitoring`）
- [x] `docs/` 更新：說明 Inspector 已內建圖表，Grafana 為進階選項

### 額外完成

- [x] `PlatformAdapter.get_band()`：從 `freq_mhz` 推導 2.4G/5G/6G，所有平台自動繼承
- [x] `LinuxAdapter._IW`：`shutil.which` 解決 systemd PATH 不含 `/usr/sbin` 的問題
- [x] `InspectorState.update_status`：retained MQTT 訊息不更新 `last_seen`，防止誤判 online
- [x] Inspector MQTT 重連導致閃爍：排查確認是兩個 inspector 搶 client ID

---

## Goal: 法律合規準備

- [x] 私人設備/時間確認、git identity、GPG、GitHub repo
- [x] 開發日誌 WORKLOG.md
- [ ] 諮詢加州執業智財/僱傭律師（California Labor Code §2870）
