# Project Targets — syncbench

_Last updated: 2026-05-14_

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
- [x] repo 轉 public（待律師確認）

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
- [x] `PlatformAdapter.get_wifi_ip()`：SIOCGIFADDR ioctl，heartbeat 帶 ip，Inspector 左欄顯示
- [x] Offline device 8 秒 grace period：選取裝置斷線 → 橘色警告 → 自動取消，可手動取消
- [x] Chart 同步修正：metronome-driven rendering（1Hz 節拍器，所有線同步前進）
- [x] Chart x 軸預先固定為完整 duration 寬度（`1s..{dur}s`），不隨時間延伸
- [x] Timer 修正：第一個 iperf3 sample 到才開始倒數，`collecting` phase 停止並凍結

---

## Goal: Phase 3 — Traffic Direction + QoS Testing

> 讓每次測試可以選擇流量方向（uplink/downlink/bidirectional）和 QoS 優先級（VO/VI/BE/BK），並在 Inspector 即時看到不同 AC class 的吞吐量差異。

### Step 1 — Traffic Direction 支援

- [x] `TrafficConfig` 加 `direction: uplink | downlink | bidirectional`
- [x] Agent：downlink 模式 spawn iperf3 server（`_TCP_SERVER_RE` 修 server 端 regex 無 retransmits 欄位）
- [x] Orchestrator：downlink 模式在 Mac 端 spawn iperf3 clients，+1.5s grace period 等 RPi bind port
- [x] Orchestrator：從 heartbeat 自動建 `_agent_ips` dict
- [x] Inspector UI：device 選取加方向切換（↑ / ↓ / ↕），Run Status 顯示方向 icon
- [x] Bidirectional（`--bidir`）：TX-C buffer + RX-C 合成，`throughput = TX + RX`

### Step 2 — QoS / DSCP 標記

- [x] `TrafficConfig` 加 `ac: vo | vi | be | bk`（自動映射 DSCP → iperf3 `--tos`）
  - VO → DSCP EF (46) → `--tos 0xb8`
  - VI → DSCP AF31 (26) → `--tos 0x68`
  - BE → DSCP 0 → `--tos 0x00`
  - BK → DSCP CS1 (8) → `--tos 0x20`
- [x] Agent：iperf3 command 加入 `--tos` 參數（uplink client path）
- [x] Orchestrator：downlink client 加 `--tos`（Mac 端 spawn client 帶 ac 參數）
- [x] AP 側確認 WMM 啟用（hostapd `wmm_enabled=1` + DSCP→TID mapping 行為驗證）
- [x] Inspector UI：device 選取加 AC 選擇（VO / VI / BE / BK），結果表顯示 AC class + TOS 值

### Step 3 — QoS 差異視覺化

- [x] 結果表：每台 agent 顯示 direction 和 AC class 欄位（含 TOS hex 值）
- [~] Chart：AC class 圖例 — skip，結果表顏色標示已足夠，曲線高低直接反映 QoS 差異
- [x] `scenarios/05_qos_vi_vs_be.yaml`：VI 跟 BE 同時下行，驗證 AP 下行優先排程（實測 194 vs 40 Mbps）
- [~] JFI 按 AC 分組顯示 — skip，單一 JFI 對 fairness 比較夠用；per-AC 可作為未來 enhancement

### Step 4 — QoS Scenario 補全 + Bidirectional

- [x] `scenarios/06_downlink_be.yaml`：純 downlink BE baseline（兩台 equal，JFI ≈ 1.0）
- [x] `scenarios/07_qos_uplink_vi_vs_be.yaml`：uplink VI vs BE，展示 WMM EDCA 不對稱（BE 211 Mbps > VI 49 Mbps）
- [x] Inspector live chart：雙向模式顯示 TX+RX 合計一條線（設計決定：fairness 比較夠用，不需分兩條）

### Step 5 — 文件 + Platform Adapter

- [x] `docs/methodology.md` 補充 DSCP mapping 表、WMM 驗證方法、三大 QoS 發現（2026-05-04）
- [x] `docs/user-guide-en.md` / `user-guide-zh.md` 補充 AC 選擇說明與方向不對稱警告
- [~] `LinuxAdapter.get_link_info()`：補充回報 DSCP / TOS 實際值 — skip，已透過測量結果驗證（VI 194 Mbps vs BE 40 Mbps）
- [~] `MacOSAdapter.get_link_info()`：補 `freq_mhz` 替換 deprecated `airport` — skip，Mac 為 orchestrator 非 agent，不影響測試

---

## Goal: Mac Agent 支援（macOS 26.x）

> 讓 MacBook 也能當 STA agent 加進 testbed。設計原則：所有改動隔離在 Mac 路徑，**不動 Linux/RPi 既有行為**。

### Step 1 — MacOSAdapter 重寫（macOS 14.4+ 沒 airport）

- [x] `get_wifi_ip()` override：用 `ipconfig getifaddr <iface>`（base.py 用 Linux ioctl `SIOCGIFADDR` 在 Mac 失敗）
- [x] `get_link_info()` 改用 `system_profiler -json SPAirPortDataType`（airport CLI 已被 Apple 移除）
- [x] 背景 daemon thread 每 30s 輪詢 link info（system_profiler 單次 ~7-8s，不能擋 1Hz heartbeat）
- [x] `get_band()` override：6E channel 1 = 5955 MHz，base.py 6000 邊界會誤判成 5G，改為 5925 MHz 邊界
- [x] 解析 SSID / BSSID / RSSI / freq_mhz / tx_rate_mbps（BSSID/SSID 受 Location Services 權限影響，正常）

### Step 2 — Mac Setup 自動化

- [x] `scripts/setup-macos.sh`：brew 裝 iperf3 + uv → `uv sync` → smoke check → LaunchAgent
- [x] LaunchAgent (`~/Library/LaunchAgents/com.atf.agent.plist`)：登入自動啟動，crash 自動重啟（KeepAlive）

### Step 3 — 驗證

- [x] Mac mini 本機 adapter smoke test（IP / MAC / link / band 全對）
- [x] 端到端：agent boot → IDLE → MQTT 心跳 → clean shutdown
- [x] 新 MacBook 上 `setup-macos.sh` 跑通（rsync repo + ssh-copy-id + LaunchAgent 上線）
- [x] Mac STA 跟 RPi 混合 scenario：`scenarios/08_mixed_mac_rpi.yaml`（downlink, mac 617 Mbps + rpi 107 Mbps）
- [x] Bug fix：LaunchAgent plist `EnvironmentVariables.PATH` 加 `/usr/sbin:/sbin`（不然 networksetup/ipconfig/system_profiler 找不到）

### Step 4 — 文件

- [x] `docs/multi-platform.md`：Mac 狀態 🟡 → ✅，補充 system_profiler 限制、Location Services 權限、6E band 修正

---

## Goal: Windows Agent 支援（Windows 10 / 11）

> 跟 macOS 一樣風格：`uv` 環境 + 手動 PowerShell launcher，不裝自動啟動。MVP 支援英文 UI；非英文 UI 為 documented caveat。透過 agent-team skill 6 角色並行完成（Architect / 2 Implementer / Writer / 2 Reviewer）。

### Step 1 — WindowsAdapter 實作

- [x] `agent/atf_agent/platform/windows.py`：所有 PlatformAdapter ABC 方法（`get_platform_info` / `get_wifi_interface` / `get_wifi_mac` / `get_link_info` / `get_wifi_ip` override / `get_ntp_offset_ms` / `is_ntp_synced`）
- [x] `netsh wlan show interfaces` 解析（SSID / BSSID / Channel→freq_mhz / Signal%→RSSI via Microsoft formula `(pct/2)-100`）
- [x] `w32tm /query /status` 解析（Phase Offset 秒→ms、Source 排除 Local CMOS / Free-running、< 24h sync age）
- [x] `get_wifi_ip()` 用 socket UDP connect trick 避開 Linux fcntl
- [x] `get_band()` 不 override（繼承 base.py）
- [x] 接進 `agent/atf_agent/main.py:_make_platform_adapter()`

### Step 2 — Setup + Launcher Scripts

- [x] `scripts/setup-windows.ps1`（admin）：admin check + winget (`Astral-sh.uv` + iperf3 雙 id fallback) + `uv sync` + idempotent firewall rule + smoke test
- [x] `scripts/run-agent.ps1`（non-admin manual launcher）
- [x] Firewall scope 限縮為 `-Profile Domain,Private -RemoteAddress LocalSubnet`（不在 Public Wi-Fi 暴露 port 5201；Security review P1 修補）

### Step 3 — 驗證（待實機）

- [ ] Windows 機器跑 `setup-windows.ps1`，確認 winget iperf3 id 實際可用（兩 id 之一）
- [ ] `run-agent.ps1` 啟動 agent → MQTT IDLE → controller 看到 online
- [ ] 跑 mixed scenario：Win + RPi 同時 uplink + downlink，確認 throughput / fairness 數據合理

### Step 4 — 文件

- [x] `docs/multi-platform.md` Support Matrix + Per-Platform Caveats Windows 區塊（netsh 速度、英文 UI 限制、Signal%→RSSI 估算、firewall scope、Wi-Fi adapter sleep 應對、winget caveats、w32time 注意事項）
- [x] Adding a New Platform Recipe：範例從 Windows 改為 Android（Windows 已完成可作為 onboarding 範本）
- [x] `docs/multi-platform-zh.md` 同步：Support Matrix + Caveats + Recipe 範例
- [x] `README.md`：Status line、Pluggable adapters bullet、Support Platforms 表、新增 Feature History "Windows station support (2026-05-11)" 段
- [x] `docs/user-guide-en.md` + `docs/user-guide-zh.md`：Agent 表加 Windows、新增 §6.9 "Adding a Windows machine as a STA"（兩語版本對齊 779 行）
- [x] 「未實機驗證」warning：Status 從 ✅ Stable 降為 🟡 Dev only；user-facing docs 全部加 warning banner（避免誤導）
- [x] 兩個 commit 推上 GitHub main（`dbf5de2` code + `1660a6d` docs）

---

## Goal: Buildroot 預燒 RPi Image

> 用 Buildroot 產生一個 default image，燒進 RPi 即內建 syncbench agent + 整合好的 systemd service，不需要手動 `setup-rpi.sh`。降低新加 STA 的部署成本。

### Step 1 — 第一版 image（已完成）

- [x] 學 Buildroot 流程，產出可開機的原始 image
- [x] 在 RPi 上實機驗證可開機

### Step 2 — Image 進 repo

- [ ] 把目前 image copy 到 `rpi-image/` 資料夾
- [ ] `rpi-image/README.md`：image 版本 / 燒錄方式 / default credentials / 已知限制

### Step 3 — Buildroot config / overlay 整合 syncbench

- [ ] Buildroot defconfig 收進 `rpi-image/configs/`（之後可重現 build）
- [ ] root filesystem overlay：預先放 `atf-agent` code（或 `uv sync` 過的環境）
- [ ] 預裝相依：`iperf3`、Python runtime、`uv`（或 wheel-based 部署）
- [ ] `atf-agent.service` 預設 enabled，開機自動跑

### Step 4 — First-boot 自動化

- [ ] hostname / agent_id 第一次開機可從 SD card config 改（不用每台手動 edit）
- [ ] Wi-Fi credentials 從外部 config 注入（不寫死在 image）
- [ ] MQTT broker 位址 / InfluxDB token 同上

### Step 5 — 文件 + 驗證

- [ ] `docs/multi-platform.md`：新增「RPi pre-baked image」章節作為部署選項
- [ ] 燒一張新卡上線新 STA，驗證 boot → 自動連 MQTT → controller 看到 online（無人工介入）
- [ ] 對比手動 `setup-rpi.sh` 流程的時間差，記錄到 README

---

## Goal: 法律合規準備

- [x] 私人設備/時間確認、git identity、GPG、GitHub repo
- [x] 開發日誌 WORKLOG.md
- [x] 諮詢加州執業智財/僱傭律師（California Labor Code §2870）— skipped
