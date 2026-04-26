# ATF Validator — Phase 1 完整規格

**版本**:1.0  
**日期**:2026-04-25  
**範圍**:Phase 1 — 5 台 STA 規模的架構驗證  
**狀態**:規劃完成,待動工

---

## 目錄

1. [專案定位](#1-專案定位)
2. [Phase 1 成功定義](#2-phase-1-成功定義)
3. [整體架構](#3-整體架構)
4. [硬體拓撲](#4-硬體拓撲)
5. [MQTT 通訊協定](#5-mqtt-通訊協定)
6. [同步啟動機制](#6-同步啟動機制)
7. [Agent 設計](#7-agent-設計)
8. [Controller Orchestrator](#8-controller-orchestrator)
9. [Inspector Web UI](#9-inspector-web-ui)
10. [Scenario YAML 規格](#10-scenario-yaml-規格)
11. [資料儲存](#11-資料儲存)
12. [Repo 結構](#12-repo-結構)
13. [實作排程(Week 1–4)](#13-實作排程week-14)
14. [風險與緩解](#14-風險與緩解)
15. [法律與授權策略(加州版)](#15-法律與授權策略加州版)
16. [動工前檢查清單](#16-動工前檢查清單)
17. [後續 Phase 預覽](#17-後續-phase-預覽)
18. [未解決事項](#18-未解決事項)

---

## 1. 專案定位

> **一個跨平台、基於開源工具鏈的 Wi-Fi Airtime Fairness 驗證框架,用於驗證 IEEE 802.11 標準 ATF 機制在多 STA 場景下的公平性表現。**

這句話是專案的「定位錨點」,出現在 README 第一行,是面對任何質疑時的根基。注意:不提任何廠商、晶片、driver 名稱。

### 1.1 三大設計原則

**跨平台(Cross-Platform)**  
系統設計支援 Linux、macOS、Windows、Android 四個平台,證明這是「通用測試架構」,不會被定義為「為任何特定 driver 量身定做的內部工具」。

**標準化協議**  
測試對象是 IEEE 802.11 標準範疇內的 Airtime Fairness 概念。所有控制指令使用 `iw`、`nl80211`、`hostapd_cli`、`ubus` 等標準介面,**絕不使用任何廠商私有 IOCTL / vendor command**。

**Upstream 依賴**  
AP 端使用 OpenWrt(開源社群維護),測試對象是 mac80211 框架下的 upstream driver(ath9k、ath10k、mt76 等),不接觸任何未上市產品。

### 1.2 Phase 規劃總覽

| Phase | 規模 | 平台 | 目標 |
|---|---|---|---|
| **Phase 1** | 1 AP + 3–5 STA | Linux only | 架構驗證、單平台跑通 |
| Phase 2 | 同 5–10 台 | Linux + macOS + Windows + Android | 跨平台抽象層驗證 |
| Phase 3 | 20 → 50 STA | 混合平台 | 規模化、broker tuning |
| Phase 4 | — | — | 文件、論文、開源發表 |

本文件只涵蓋 Phase 1。

---

## 2. Phase 1 成功定義

Phase 1 完成 ⇔ 以下五點全部達成:

1. **單指令執行**:Mac 終端機輸入 `atf-run scenarios/01_two_sta_equal.yaml`,3–5 台 RPi 同時啟動 iperf3
2. **即時可視化**:Grafana 可看到每台 STA 的吞吐量曲線
3. **自動報告**:測試結束自動產出 markdown 報告(Jain's Fairness Index、各 STA 平均吞吐、retry 率)
4. **同步精度量化**:STA 起跑時間誤差實測 <100 ms(`sync_offset_ms` 欄位)
5. **可重現性**:系統重啟後 5 分鐘內能再次跑同樣測試

達不到這 5 點,Phase 1 不算完成。

---

## 3. 整體架構

### 3.1 邏輯架構

```
┌─────────────────────────────────────────────────────────┐
│                    Controller (Mac)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Orchestrator │  │  MQTT Broker │  │  InfluxDB    │  │
│  │  (Python)    │  │  (Mosquitto) │  │  + Grafana   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │          │
│  ┌──────┴───────┐          │                  │          │
│  │  Inspector   │          │                  │          │
│  │  Web UI      │          │                  │          │
│  │  + SQLite    │          │                  │          │
│  └──────────────┘          │                  │          │
└─────────────────────────────┼──────────────────┴──────────┘
                              │ MQTT over TCP (管理網路)
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   ┌─────────┐           ┌─────────┐           ┌─────────┐
   │ Agent   │           │ Agent   │   ...     │ AP      │
   │ (RPi)   │           │ (RPi)   │           │collector│
   │─────────│           │─────────│           │(OpenWrt)│
   │ iperf3  │           │ iperf3  │           │ ubus    │
   │ runner  │           │ runner  │           │ debugfs │
   │ stats   │           │ stats   │           │ reader  │
   │ reporter│           │ reporter│           │         │
   └─────────┘           └─────────┘           └─────────┘
   STA-01                STA-02 ... STA-05      AP
```

### 3.2 設計關鍵

**Agent-based,非中央輪詢**  
每台 STA 跑一個輕量 agent,主動上報狀態與指標。Controller 透過 MQTT pub/sub 廣播指令,所有 agent 同時收到、同時執行。

**為什麼不用 SSH fan-out**  
50 台 SSH 連線管理複雜、起跑時間漂移大、跨平台 SSH 行為不一致。MQTT 解決全部問題:

| 需求 | SSH 方案 | MQTT 方案 |
|---|---|---|
| 50 台同步啟動 | 50 條獨立連線,時序漂移 | 一次 broadcast,毫秒級同步 |
| 跨平台 | 三平台 SSH 行為不同 | MQTT client 庫三平台統一 |
| 連線斷掉 | 整個測試壞掉 | QoS + LWT,自動重連 |
| Agent 數成長 | O(N) 連線管理 | broker 處理,O(1) for controller |
| Android | 幾乎不可能 | Paho MQTT 庫成熟 |

**管理網路與測試網路分離**  
所有控制流量走 Ethernet(管理網路),Wi-Fi 只承載 iperf3 測試流量。控制不會污染測試結果。

---

## 4. 硬體拓撲

```
                    ┌──────────────────┐
                    │   Mac (筆電)      │
                    │  Controller +    │
                    │  Mosquitto +     │
                    │  InfluxDB +      │
                    │  Grafana (Docker)│
                    └────────┬─────────┘
                             │ Ethernet (管理網路)
                    ┌────────┴─────────┐
                    │  Management      │
                    │  Switch          │
                    └──┬────┬────┬──┬──┘
                       │    │    │  │
              ┌────────┘    │    │  └────────┐
              ▼             ▼    ▼           ▼
        ┌──────────┐  ┌────────┐ ┌────────┐ ┌──────────┐
        │ OpenWrt  │  │ RPi #1 │ │ RPi #2 │ │ RPi #3-5 │
        │ AP (DUT) │  │ STA-A  │ │ STA-B  │ │ STA-C-E  │
        └────┬─────┘  └───┬────┘ └───┬────┘ └────┬─────┘
             │            │          │           │
             └────────────┴──────────┴───────────┘
                  Wi-Fi (5 GHz, 隔離環境)
```

### 4.1 硬體清單

| 元件 | 數量 | 規格 | 用途 |
|---|---|---|---|
| Mac (筆電) | 1 | macOS,8GB+ RAM | Controller + Docker stack |
| OpenWrt AP | 1 | 待定型號 | DUT(被測 AP) |
| Raspberry Pi 4 | 3–5 | 8GB RAM | STA agent |
| USB Wi-Fi dongle | 3–5(備用) | 802.11ac | 若 RPi 內建 Wi-Fi 不足時替換 |
| 管理 Switch | 1 | 8-port Gigabit | 連接管理網路 |
| Ethernet 線材 | ~7 | Cat 5e+ | 管理網路 |

### 4.2 RF 環境注意事項

**Phase 1 規模(5 台)在普通家庭環境可運作**,但要避免:
- 隔壁鄰居 Wi-Fi 干擾(用 5 GHz 頻段較乾淨)
- 微波爐運作時段
- 藍牙裝置密集區域(2.4 GHz)

未來 50 台規模需要 RF 隔離環境(shielded box 或 anechoic chamber),Phase 1 不需要考慮。

---

## 5. MQTT 通訊協定

### 5.1 設計原則

1. **JSON payload + UTF-8**:可讀性與 debug 友好優先,Phase 1 不需要 binary protocol 的效能
2. **每個 payload 含 envelope**:`v`(schema 版本)、`ts`(unix epoch ms)、`msg_id`(ULID/UUID)
3. **Topic 不放動態資料**:動態資料放 payload,topic 結構穩定
4. **QoS 對應語意**:
   - QoS 0:高頻 metric(heartbeat、即時 stats)
   - QoS 1:指令、ack、status、capability
   - QoS 2:不使用
5. **Retained message**:status / capabilities 用 retained;heartbeat / metrics / commands 不 retained
6. **LWT (Last Will and Testament)**:每個 agent 設定 LWT 在 `agent/{id}/status`,斷線時 broker 自動 publish offline

### 5.2 Topic 完整列表

```
atf/
│
├── ctrl/                                    [Controller → Agents/AP]
│   ├── broadcast/
│   │   ├── prepare                         QoS 1
│   │   ├── start_at                        QoS 1
│   │   ├── stop                            QoS 1
│   │   └── teardown                        QoS 1
│   └── unicast/
│       ├── agent/{agent_id}/
│       │   ├── connect_wifi                QoS 1
│       │   ├── set_traffic                 QoS 1
│       │   ├── exec                        QoS 1
│       │   └── request_capabilities        QoS 1
│       └── ap/{ap_id}/
│           ├── configure                   QoS 1
│           └── reset_stats                 QoS 1
│
├── agent/{agent_id}/                        [Agent → Controller]
│   ├── status                              QoS 1, retained, LWT
│   ├── capabilities                        QoS 1, retained
│   ├── heartbeat                           QoS 0, 1Hz
│   ├── ack/{cmd_id}                        QoS 1
│   ├── metrics/
│   │   ├── wifi                            QoS 0, 1Hz
│   │   ├── traffic                         QoS 0, 1Hz
│   │   └── system                          QoS 0, 0.2Hz (5s)
│   ├── result/{run_id}                     QoS 1
│   └── log                                 QoS 1 (error/warning 才推)
│
└── ap/{ap_id}/                              [AP collector → Controller]
    ├── status                              QoS 1, retained, LWT
    ├── capabilities                        QoS 1, retained
    ├── stations/{sta_mac}                  QoS 0, 1Hz
    └── airtime                             QoS 0, 1Hz
```

### 5.3 Common Envelope

每個 payload 必含:
```json
{
  "v": 1,
  "ts": 1745571825123,
  "msg_id": "01HXAA8JB7QZK4M3N5P9R2T6X8",
  "...": "...payload-specific fields..."
}
```

### 5.4 關鍵 Payload 範例

#### `atf/ctrl/broadcast/prepare`

```json
{
  "v": 1, "ts": 1745571825123, "msg_id": "01HXAA8JB7Q...",
  "run_id": "run-20260425-102345-3f7a",
  "scenario_name": "01_two_sta_equal",
  "expected_agents": ["rpi-sta-01", "rpi-sta-02"],
  "ap_id": "openwrt-ap-01",
  "wifi": {
    "ssid": "ATF-TEST-5G",
    "psk": "test-only-2026",
    "bssid": "11:22:33:44:55:66",
    "band": "5",
    "channel": 36
  },
  "phase_timeout_sec": 30
}
```

#### `atf/ctrl/broadcast/start_at`

```json
{
  "v": 1, "ts": 1745571830050, "msg_id": "01HXAA8KP3R...",
  "run_id": "run-20260425-102345-3f7a",
  "start_unix_ms": 1745571835000,
  "duration_sec": 60,
  "warmup_sec": 5
}
```

**約束**:`start_unix_ms` 必須晚於 broadcast 時間至少 2 秒。

#### `agent/{agent_id}/status` (retained + LWT)

```json
{
  "v": 1, "ts": 1745571820000,
  "state": "ARMED",
  "agent_version": "0.1.0",
  "platform": "linux",
  "current_run_id": "run-20260425-102345-3f7a",
  "last_error": null
}
```

`state` 可選值:`BOOT` / `IDLE` / `PREPARING` / `ARMED` / `RUNNING` / `REPORTING` / `ERROR` / `OFFLINE`

#### `agent/{agent_id}/capabilities` (retained)

完整四層 capability 結構,涵蓋:

- **platform**:OS / kernel / arch / model
- **connectivity** (Layer 1):associated / bssid / ssid / rssi / freq / channel
- **phy** (Layer 2):standard / bandwidth / spatial_streams / max_mcs / driver
- **qos_atf** (Layer 3):wmm / uapsd / ampdu / amsdu
- **software** (Layer 4):agent_version / ntp_synced / ntp_offset_ms / mqtt_rtt_ms / iperf3_version / cpu / mem

#### `agent/{agent_id}/result/{run_id}`

```json
{
  "v": 1, "ts": 1745571895000, "msg_id": "01HXAA8X1Z3...",
  "run_id": "run-20260425-102345-3f7a",
  "agent_id": "rpi-sta-01",
  "status": "complete",
  "actual_start_ms": 1745571835012,
  "actual_end_ms": 1745571895008,
  "sync_offset_ms": 12,
  "summary": {
    "throughput_mean_mbps": 92.4,
    "throughput_stdev_mbps": 3.1,
    "throughput_p50_mbps": 92.7,
    "throughput_p95_mbps": 96.2,
    "total_bytes": 692908032,
    "total_retransmits": 87,
    "loss_pct": 0.05
  }
}
```

`sync_offset_ms` 是驗證 <100 ms 同步精度的核心證據。

### 5.5 Topic 訂閱規劃

**Controller (orchestrator)**:
```
agent/+/status, agent/+/ack/+, agent/+/result/+, agent/+/log, ap/+/status
```

**Controller (inspector + influx writer)**:
```
agent/+/capabilities, agent/+/heartbeat, agent/+/metrics/+
ap/+/capabilities, ap/+/stations/+, ap/+/airtime
```

**Agent**:
```
atf/ctrl/broadcast/+, atf/ctrl/unicast/agent/{my_id}/+
```

**AP collector**:
```
atf/ctrl/broadcast/+, atf/ctrl/unicast/ap/{my_id}/+
```

### 5.6 Schema 演進策略

- Phase 1 = `v: 1`
- 新增欄位:**只加不改不刪**,接收端 `extra='ignore'`
- Breaking change:升 `v: 2`,雙版本支援一段時間
- `v` 不符:drop + log warning

### 5.7 訊息量估算

5 台 STA 跑測試時:**約 6 KB/s**,Mosquitto 在 RPi 上都跑得動,Mac 上完全不是問題。

### 5.8 Broker 設定 (Mosquitto)

```
listener 1883
allow_anonymous true       # Phase 1 內網,先不做 auth
max_inflight_messages 200
max_queued_messages 1000
persistence true
persistence_location /mosquitto/data/
log_dest stdout
log_type error
log_type warning
log_type notice
```

Phase 4 開源前要加 username/password + TLS。

---

## 6. 同步啟動機制

### 6.1 目標

STA 起跑時間誤差 **<100 ms**(實測,非假設)

### 6.2 機制:NTP 對時 + 「未來時間戳」啟動

```
T=0:    Controller 廣播 prepare
        payload: {run_id, scenario, expected_agents}
        
T=0~3:  Agents 收到 → 連線 / 配置 iperf3 / 跑 preflight → 回 ack
        
T=3:    Controller 確認所有 agent ack
        若有 agent 沒 ack → abort
        
T=4:    Controller 廣播 start_at
        payload: {start_unix_ms: 現在 + 5000ms}
        
T=4~9:  Agents 收到 → 計算 delta → time.sleep_until(start_unix_ms)
        
T=9:    所有 STA 同時啟動 iperf3
```

### 6.3 實作關鍵

1. **NTP 必須先到位**:所有 RPi 開機跑 `chronyd` 對時。RPi 之間 NTP offset 通常 <5 ms
2. **回報 NTP offset 當 metric**:`chronyc tracking` 的 offset 進 InfluxDB,dashboard 顯示「實際同步精度」
3. **使用 absolute deadline**:`time.clock_gettime(CLOCK_REALTIME)` 比對,差異 <10 ms 才呼叫 iperf3
4. **iperf3 啟動延遲**:fork + exec 約 20–50 ms,要在 result 回報的 `sync_offset_ms` 中量化

### 6.4 量測方式

每個 agent 在 result payload 中回報 `sync_offset_ms = actual_first_packet_ms - start_unix_ms`,Grafana 用一個專門 panel 顯示所有 agent 的 sync offset 分布。

---

## 7. Agent 設計

### 7.1 狀態機

```
                ┌──────────┐
                │  BOOT    │  agent 程序啟動
                └────┬─────┘
                     │ NTP 同步完成 + MQTT 連上
                     ▼
                ┌──────────┐
        ┌──────│  IDLE    │←─────────┐
        │       └────┬─────┘          │
        │            │ prepare 收到    │ teardown
        │            ▼                │
        │       ┌──────────┐          │
        │       │ PREPARING│──────────┤ error
        │       └────┬─────┘          │
        │            │ ready ack      │
        │            ▼                │
        │       ┌──────────┐          │
        │       │  ARMED   │──────────┤ stop / timeout
        │       └────┬─────┘          │
        │            │ start_at 觸發  │
        │            ▼                │
        │       ┌──────────┐          │
        │       │ RUNNING  │──────────┤
        │       └────┬─────┘          │
        │            │ 測試結束       │
        │            ▼                │
        │       ┌──────────┐          │
        └──────│ REPORTING │──────────┘
                └──────────┘
```

每個狀態變化都 publish 到 `agent/{id}/status` (retained)。

### 7.2 模組結構

```
agent/atf_agent/
├── __init__.py
├── main.py                  # 入口 + state machine
├── mqtt_bus.py              # 共用 MQTT 邏輯
├── platform/
│   ├── base.py              # PlatformAdapter 抽象
│   └── linux.py             # Phase 1 只實作這個
├── traffic/
│   └── iperf3.py            # 啟動/解析 iperf3
├── monitor/
│   ├── wifi_link.py         # iw dev wlan0 link 解析
│   └── system.py            # CPU/mem/NTP
├── capability/
│   ├── collector.py         # 主協調者
│   ├── linux_phy.py         # 解析 iw phy info
│   ├── linux_link.py        # 解析 iw dev link
│   ├── linux_qos.py         # WMM/AMPDU 偵測
│   └── linux_software.py    # chronyc / mqtt rtt / cpu
└── sync.py                  # NTP-aware sleep_until
```

### 7.3 PlatformAdapter 抽象

Phase 1 只實作 Linux,但介面要為 Phase 2 鋪好:

```python
class PlatformAdapter(ABC):
    @abstractmethod
    def get_wifi_interface(self) -> str: ...
    
    @abstractmethod
    def get_link_info(self) -> LinkInfo: ...
    
    @abstractmethod
    def get_phy_capabilities(self) -> PhyCaps: ...
    
    @abstractmethod
    def connect_wifi(self, ssid: str, psk: str, bssid: str) -> None: ...
    
    @abstractmethod
    def disconnect_wifi(self) -> None: ...
    
    @abstractmethod
    def start_iperf3_client(self, params: Iperf3Params) -> Iperf3Process: ...
```

Phase 2 加 `MacosAdapter`、`WindowsAdapter`、`AndroidAdapter`,介面不變。

---

## 8. Controller Orchestrator

### 8.1 主流程

```python
async def run_scenario(scenario_path: str):
    # 1. 載入 scenario,驗證 schema
    scenario = load_scenario(scenario_path)
    
    # 2. 產生 run_id
    run_id = generate_run_id()
    
    # 3. Preflight check(從 inspector state 取資料)
    preflight = check_preflight(scenario, inspector.state)
    if not preflight.passed:
        write_preflight_to_sqlite(preflight)
        raise PreflightFailedError(preflight.issues)
    
    # 4. Configure AP via ubus
    await configure_ap(scenario.ap)
    
    # 5. Broadcast prepare
    cmd_id = await mqtt.publish_broadcast("prepare", scenario)
    
    # 6. Wait for all agent acks (timeout: scenario.phase_timeout_sec)
    acks = await wait_for_acks(scenario.expected_agents, cmd_id)
    if not all(a.ok for a in acks):
        await abort_run(run_id, "prepare_failed")
        return
    
    # 7. Calculate start time, broadcast start_at
    start_ms = now_ms() + 5000
    await mqtt.publish_broadcast("start_at", {start_unix_ms: start_ms, ...})
    
    # 8. 進入監控階段:訂閱 metrics、寫 InfluxDB(由 influx_writer 處理)
    await wait_for_test_complete(run_id, scenario.duration_sec + 10)
    
    # 9. Broadcast stop, collect results
    await mqtt.publish_broadcast("stop", {run_id, reason: "normal_complete"})
    results = await collect_results(scenario.expected_agents, run_id)
    
    # 10. Generate report
    report = generate_report(scenario, results, run_id)
    save_report(run_id, report)
    
    # 11. Teardown
    await mqtt.publish_broadcast("teardown", {run_id})
```

### 8.2 模組結構

```
controller/atf_ctrl/
├── cli.py                    # `atf-run`, `atf-status` 入口
├── orchestrator.py           # 主流程狀態機
├── mqtt_bus.py               # MQTT client 包裝
├── scenarios/
│   ├── loader.py             # YAML + extends 合併
│   ├── models.py             # Pydantic models
│   └── validator.py
├── protocol/                 # MQTT payload Pydantic models
│   ├── envelope.py
│   ├── ctrl/                 # 下行指令
│   ├── agent/                # 上行
│   ├── ap/
│   └── topics.py             # topic builder/parser
├── agent_registry.py         # 追蹤 online agents
├── influx_writer.py          # 訂閱 metrics → InfluxDB
├── reporter.py               # 跑完出報告
├── inspector/                # Web UI
│   ├── server.py             # FastAPI app
│   ├── state.py              # InspectorState
│   ├── sse.py                # SSE 推送
│   ├── checks/               # L1-L4 檢查邏輯
│   ├── storage/              # SQLite
│   └── templates/            # HTMX HTML
└── metrics/
    ├── jain_fairness.py      # Jain's Fairness Index
    └── airtime_calc.py       # 從 stats 推算 airtime
```

### 8.3 Report 輸出格式

每次測試結束產出:

- `reports/{run_id}/report.md` — 主報告(markdown)
- `reports/{run_id}/charts/*.png` — 吞吐量、RSSI、sync offset 圖表
- `reports/{run_id}/raw/*.json` — 原始 result payload(供事後分析)
- `reports/{run_id}/preflight.md` — 環境合格證明

---

## 9. Inspector Web UI

### 9.1 功能

跑在 Mac controller 上的獨立 Web UI,即時顯示:

- AP 狀態與能力
- 所有預期 agent 的連線狀態
- 每個 agent 的四層能力(L1 連線 / L2 PHY / L3 QoS-ATF / L4 軟體)
- Preflight 檢查結果與「Run Test」按鈕
- 歷史 preflight 紀錄(SQLite)

### 9.2 技術選型

| 元件 | 選擇 | 理由 |
|---|---|---|
| Web 框架 | FastAPI | 跟 controller 同 Python 生態,async 原生 |
| 即時推送 | Server-Sent Events (SSE) | 比 WebSocket 簡單,單向夠用 |
| 前端 | HTMX + Tailwind (CDN) | 不寫 JS、不打包 |
| 模板 | Jinja2 | FastAPI 內建 |
| 即時 state | 記憶體 dict | 重啟由 retained message 重建 |
| 歷史 state | SQLite | 單檔、SQL 查詢、零配置 |

### 9.3 資料流

```
Agent ──MQTT──→ Controller MQTT subscriber ──→ Inspector State Store
                                                       │
                                                       ├──→ FastAPI + SSE ──→ Browser
                                                       └──→ SQLite (preflight runs)
```

### 9.4 四層能力檢查

| Layer | 檢查項目 | 資料來源 |
|---|---|---|
| **L1 Connectivity** | associated / bssid / rssi / connected_sec | `iw dev wlan0 link` |
| **L2 PHY** | standard / bandwidth / spatial_streams / max_mcs | `iw phy phy0 info` |
| **L3 QoS/ATF** | wmm / uapsd / ampdu / amsdu | `iw phy info` + AP 端 debugfs |
| **L4 Software** | agent_version / ntp_synced / ntp_offset / mqtt_rtt / iperf3_version | agent 自身 |

每層都有 `pass / warn / fail` 三級判定,門檻定義在 scenario YAML 中。

### 9.5 UI 草圖

```
┌────────────────────────────────────────────────────────────────┐
│  ATF Validator — Environment Status            [Run Test ▶]   │
├────────────────────────────────────────────────────────────────┤
│  AP Status                                                     │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ openwrt-ap-01  ●online  ATF: ✓  SSID: ATF-TEST-5G        │ │
│  │ Channel: 36 (80MHz)  Tx Power: 23 dBm  Uptime: 4h        │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  Connected Stations (3 / 5 expected)             ⚠ 2 missing  │
│  ┌──────────┬─────────────┬──────┬───────┬──────┬───────────┐│
│  │Agent ID  │ MAC         │ RSSI │ Rate  │ Caps │ Software  ││
│  ├──────────┼─────────────┼──────┼───────┼──────┼───────────┤│
│  │rpi-sta-01│aa:bb:..:01  │ -42  │866Mbps│ ✓✓✓  │  ✓        ││
│  │rpi-sta-02│aa:bb:..:02  │ -48  │780Mbps│ ✓✓✓  │  ✓        ││
│  │rpi-sta-03│aa:bb:..:03  │ -55  │650Mbps│ ✓✓⚠  │  ✓        ││
│  │rpi-sta-04│   ---       │ ---  │ ---   │ ---  │  offline  ││
│  │rpi-sta-05│   ---       │ ---  │ ---   │ ---  │  offline  ││
│  └──────────┴─────────────┴──────┴───────┴──────┴───────────┘│
│                                                                │
│  Pre-flight Check: ⚠ Not Ready (2 STA offline)                │
└────────────────────────────────────────────────────────────────┘
```

### 9.6 API 端點

```
GET  /                           # Dashboard 主頁
GET  /sta/{agent_id}             # 單 STA 詳細頁
GET  /events/state               # SSE stream
GET  /api/state                  # JSON snapshot
GET  /api/agents                 # 所有 agent 列表
GET  /api/agents/{id}            # 單一 agent 詳細
GET  /api/preflight              # 跑一次 preflight
POST /api/preflight/report       # 產出 markdown 報告
GET  /api/history/preflight      # 歷史 preflight 列表
GET  /api/history/preflight/{id} # 特定一次完整 state
GET  /api/history/compare?a=&b=  # 比較兩次差異
GET  /healthz
```

---

## 10. Scenario YAML 規格

### 10.1 完整範例

```yaml
# scenarios/02_asymmetric_rate.yaml
extends: _base/strict.yaml

name: "Asymmetric Rate ATF Test"
description: "Verify ATF protects slow STA from being starved by fast STA"
reference: "IEEE 802.11-2020"

# === Preflight requirements ===
preflight:
  expected_agents: ["rpi-sta-01", "rpi-sta-02"]
  
  connectivity:
    rssi_dbm:
      fail_below: -75
      warn_below: -65
    must_be_associated: true
    min_connected_sec: 10
  
  phy:
    min_standard: "802.11ac"
    min_bandwidth_mhz: 80
    min_spatial_streams: 2
    required_bands: ["5"]
  
  qos_atf:
    wmm_required: true
    ampdu_required: true
    ap_atf_required: true
  
  software:
    ntp_synced_required: true
    max_ntp_offset_ms: 50
    warn_ntp_offset_ms: 20
    max_mqtt_rtt_ms: 100
    min_cpu_idle_pct: 70
    min_mem_free_mb: 200

# === AP setup ===
ap:
  node: openwrt-ap-01
  config:
    channel: 36
    bandwidth_mhz: 80
    atf_enabled: true

# === Test execution ===
duration_sec: 60
warmup_sec: 5

stations:
  - node: rpi-sta-01
    rate_constraint: "MCS7"
    traffic:
      type: iperf3_udp
      bandwidth_mbps: 100
      direction: downlink
  - node: rpi-sta-02
    rate_constraint: "MCS2"
    traffic:
      type: iperf3_udp
      bandwidth_mbps: 100
      direction: downlink

# === Success criteria ===
success_criteria:
  jains_fairness_index:
    min: 0.95
  min_throughput_ratio:
    min: 0.4
  max_packet_loss_pct: 1.0
```

### 10.2 Extends 機制

避免每個 scenario 重複大塊 preflight 設定。Base files 放 `scenarios/_base/`:

- `_base/strict.yaml`:嚴格門檻(RSSI -65 fail)
- `_base/normal.yaml`:標準門檻(RSSI -75 fail)
- `_base/lenient.yaml`:寬鬆門檻(快速 debug 用)

載入時 deep-merge,scenario 只覆寫需要的欄位。

### 10.3 Pydantic Models

`controller/atf_ctrl/scenarios/models.py` 定義所有 Pydantic models,scenario 載入時強型別驗證,YAML 寫錯立即報錯。

---

## 11. 資料儲存

### 11.1 InfluxDB Schema(時序資料)

```
measurement: wifi_link
  tags: run_id, agent_id, sta_mac, scenario_name
  fields: rssi (int), mcs (int), tx_rate_mbps (float),
          rx_rate_mbps (float), noise_floor (int)
  freq: 1Hz from each agent

measurement: traffic
  tags: run_id, agent_id, direction, protocol
  fields: throughput_mbps, retransmits, jitter_ms, lost_pct
  freq: 1Hz from iperf3 --json

measurement: ap_station_stats
  tags: run_id, ap_id, sta_mac
  fields: tx_bytes, tx_packets, tx_retries, tx_failed,
          signal_dbm, inactive_time_ms
  freq: 1Hz from AP debugfs

measurement: sync_quality
  tags: run_id, agent_id
  fields: ntp_offset_ms, mqtt_rtt_ms
  freq: 1Hz
```

`run_id` 是 tag(可過濾),不是 field。

### 11.2 SQLite Schema(歷史 / 環境快照)

```sql
CREATE TABLE preflight_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL UNIQUE,
    scenario_name   TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    overall_status  TEXT NOT NULL,
    expected_count  INTEGER,
    online_count    INTEGER,
    ready_count     INTEGER,
    full_state_json TEXT NOT NULL
);

CREATE TABLE preflight_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    layer       TEXT NOT NULL,
    message     TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES preflight_runs(run_id)
);

CREATE TABLE capability_changes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id  TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    layer     TEXT NOT NULL,
    field     TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);
```

### 11.3 保留策略

```yaml
inspector:
  sqlite_path: "./data/inspector.db"
  retention:
    preflight_runs: 200          # 最近 200 次
    capability_changes_days: 30  # 30 天
  vacuum_on_startup: true
```

---

## 12. Repo 結構

```
atf-validator/
├── README.md                   # 第一句:定位錨點
├── LICENSE                     # Apache 2.0
├── NOTICE                      # 第三方 attribution
├── CONTRIBUTING.md             # DCO 規則
├── CODE_OF_CONDUCT.md          # Contributor Covenant
├── SECURITY.md
├── pyproject.toml
├── docker-compose.yml          # mosquitto + influxdb + grafana
│
├── controller/
│   ├── atf_ctrl/
│   │   ├── cli.py
│   │   ├── orchestrator.py
│   │   ├── mqtt_bus.py
│   │   ├── scenarios/
│   │   ├── protocol/
│   │   ├── agent_registry.py
│   │   ├── influx_writer.py
│   │   ├── reporter.py
│   │   ├── inspector/
│   │   └── metrics/
│   └── tests/
│
├── agent/
│   ├── atf_agent/
│   │   ├── main.py
│   │   ├── mqtt_bus.py
│   │   ├── platform/
│   │   ├── traffic/
│   │   ├── monitor/
│   │   ├── capability/
│   │   └── sync.py
│   └── tests/
│
├── ap/
│   ├── ap_collector/
│   │   ├── main.py
│   │   ├── mqtt_bus.py
│   │   └── debugfs_reader.py
│   └── deploy/
│       ├── setup-ap.sh
│       └── hostapd.conf.j2
│
├── scenarios/
│   ├── _base/
│   │   ├── strict.yaml
│   │   ├── normal.yaml
│   │   └── lenient.yaml
│   ├── 00_smoke_test.yaml
│   ├── 01_two_sta_equal.yaml
│   ├── 02_three_sta_equal.yaml
│   └── 03_asymmetric_rate.yaml
│
├── deploy/
│   ├── rpi-image/
│   │   ├── Dockerfile
│   │   └── provision.sh
│   └── grafana/
│       └── dashboards/
│           ├── live-overview.json
│           └── sync-quality.json
│
└── docs/
    ├── architecture.md         # 本文件精簡版
    ├── methodology.md          # ATF 驗證方法學
    ├── hardware-bom.md         # 硬體清單
    ├── capability-matrix.md    # 四層能力矩陣
    ├── dependency-licenses.md  # 自動生成
    └── references.md           # IEEE 標準、論文
```

---

## 13. 實作排程(Week 1–4)

### Week 1:基礎設施 + 一台 RPi 跑通

- Mac 上 docker-compose 起 mosquitto + influxdb + grafana,確認三者互通
- 寫 `mqtt_bus.py`(controller / agent 共用基礎)
- 一台 RPi 燒好 image、跑得起 agent,publish heartbeat
- Inspector server skeleton(空頁面 + state store 框架)
- **里程碑**:Mac 上看到「rpi-sta-01 online, NTP offset 2.3 ms」

### Week 2:單 STA 測試端到端

- 寫 `iperf3.py` runner(JSON output 解析 + 1Hz throughput 回報)
- OpenWrt AP 上設好 hostapd(**等型號確定**)
- 寫 `scenarios/loader.py`,能 load YAML、validate schema
- Controller 寫 prepare / start_at / stop 最小流程
- Agent 寫 capability collector(L1 + L4 先做)→ Inspector 顯示
- 跑 `00_smoke_test.yaml`:1 台 STA 連 AP,跑 30 秒 iperf3
- **里程碑**:`atf-run 00_smoke_test.yaml` 一條指令跑完整流程

### Week 3:擴到 3 台 STA + 同步驗證

- 複製 RPi image 到 3 台,共 3 台 STA
- 跑 `01_two_sta_equal.yaml` / `02_three_sta_equal.yaml`
- 寫 `sync.py` 的 NTP-aware sleep_until,量實際 sync offset
- Grafana 加 sync-quality dashboard
- AP 端 collector 接上,讀 debugfs 寫 InfluxDB
- 補完 capability collector(L2 + L3),Inspector 完整顯示四層
- SQLite store 接上 preflight,history sidebar
- **里程碑**:3 台 STA 同步 <100 ms 已量化證明

### Week 4:報告產出 + 文件 + 規模到 5 台

- 寫 `reporter.py`:測完自動產 markdown + PNG 圖表
- 加 Jain's Fairness Index 計算
- 跑 `03_asymmetric_rate.yaml`,驗證 ATF on/off 對 fairness 差異
- 補完 `docs/architecture.md` 與 `docs/methodology.md`
- 加到 5 台 STA,做最後規模壓測
- **里程碑**:Phase 1 成功定義 5 條全部達成,專案可乾淨 push 到 GitHub

---

## 14. 風險與緩解

| 風險 | 機率 | 影響 | 緩解 |
|---|---|---|---|
| OpenWrt AP ATF 支援不完整 | 中 | 高 | 確認型號後立刻驗證;選 ath9k/ath10k/ath11k 優先 |
| RPi 內建 Wi-Fi 在多 STA 測試品質差 | 高 | 中 | 預留 USB Wi-Fi dongle 預算 |
| 同步精度 >100 ms | 中 | 中 | 量測後再決定是否需要 PTP |
| Wi-Fi 環境干擾 | 高 | 中 | 5 GHz DFS 頻段 + 晚上跑測試 |
| MQTT broker 撐不住 | 低(Phase 1 不會遇到) | 低 | Phase 3 再處理 |
| iperf3 多 stream aggregate 不準 | 中 | 低 | Phase 1 先用單 stream |

---

## 15. 法律與授權策略(加州版)

> **重要免責聲明**:本節內容基於公開可查的加州法律原則整理,**作者非律師**。實際執行前請諮詢加州執業的智財/僱傭律師,單次諮詢費用約 $500–1500,以矽谷工程師薪資水平,**這筆投資絕對划算**。

### 15.1 加州法律環境

**California Labor Code §2870** 規定員工發明在以下條件**全部成立**時屬於員工:

1. 完全在自己的時間完成
2. 沒有使用僱主的設備、用品、設施、商業機密資訊
3. 不是在僱主**現有業務或可預見研究範圍**內
4. 不是因僱主指派的工作所產生

第 3 條是真正風險:Wi-Fi driver 工程師寫 Wi-Fi ATF 測試框架,可能被主張在「可預見研究範圍」內。

**Business and Professions Code §16600**:加州競業條款幾乎全部無效,離職後限制不能執行。

**「Inevitable disclosure doctrine」**在加州不被承認,對你有利。

### 15.2 License 選擇:Apache 2.0

理由:

| License | 防衛力 | 商用友好 | 加州科技公司接受度 |
|---|---|---|---|
| MIT | 弱(無專利條款) | 高 | 高 |
| **Apache 2.0** | **強(含明示專利授權 + 反訴條款)** | **高** | **最高** |
| GPLv3 | 強(copyleft) | 低 | 中 |

**Apache 2.0 比 MIT 多了關鍵的專利反訴條款**:任何人若起訴某使用者主張該軟體侵權,該人即喪失授權。萬一未來有專利爭議,這層保護很重要。

### 15.3 Apache 2.0 必做事項

1. **原始碼檔案頂部加 license header**:
```python
# Copyright 2026 Your Name
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
```

工具:`addlicense`(Google 出的)批次自動加。

2. **NOTICE 檔精準維護**:每個第三方依賴的 NOTICE 內容要 propagate(Apache 2.0 §4 法律義務)

3. **CONTRIBUTING.md 含 DCO**:用 Developer Certificate of Origin 取代 CLA,每個 commit 加 `Signed-off-by:` 一行

### 15.4 Commit 紀律

#### Rule 1:身份完全切割

```bash
# 在 repo 內(不要 --global)
cd ~/projects/atf-validator
git config user.name "Your Personal Name"
git config user.email "your-personal@gmail.com"
```

- ✅ 個人 Gmail / ProtonMail / 自有 domain
- ❌ 絕不用 `you@company.com`

**GPG 簽署 commit**:
```bash
gpg --full-generate-key
git config user.signingkey <KEY_ID>
git config commit.gpgsign true
```

**SSH key 切割**:
```
# ~/.ssh/config
Host github.com-personal
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_personal
    IdentitiesOnly yes
```

#### Rule 2:硬體隔離(最強證據)

理想狀態:**100% 個人擁有的設備、100% 個人網路**。

具體要求:
- 個人 Mac(自己買的,公司無 MDM/監控)
- 個人 Wi-Fi(家用網路或個人手機熱點)
- 個人雲端(個人 Apple ID / Google account)
- 不使用任何公司提供的雲端服務(Workspace / O365 / GitHub Enterprise)

加州對「使用僱主資源」解釋偏嚴格,連「公司給的網路津貼補貼的網路」都可能算,安全起見全避開。

如果只有一台 Mac:**強烈建議買一台便宜二手 Mac mini 專用**($300–500 換清晰證據鏈,絕對值得)。

#### Rule 3:時間紀律

- 工作日 9:00–18:00:**不 commit**
- 平日晚上 19:00 後、週末、國定假日:OK
- **絕不偽造 timestamp**(`git commit --amend --date=...` 是地雷)

Git commit timestamp 永久保存在 GitHub,形成自動化時序證據。

#### Rule 4:Commit message 紀律

每個 commit message 都是未來爭議時被法官讀的文件。

✅ 好範例:
```
Add iperf3 runner with JSON output parsing

Implements traffic.iperf3 module wrapping iperf3 CLI with --json.
References:
- iperf3 docs: https://iperf.fr/iperf-doc.php
- IEEE 802.11-2020 Section 10 (QoS)

Tested against iperf3 3.16 on RPi 4.
```

❌ 絕對不要:
```
Fix the bug we discussed yesterday          ❌「我們」是誰?
Apply XYZ workaround                        ❌ XYZ 是公司代號?
Match production behavior                   ❌ 哪個 production?
Update per Foo's review                     ❌ Foo 是同事?
```

#### Rule 5:Commit 內容絕不出現

- 公司名、產品線代號、內部專案代號
- 同事姓名
- 公司內網 IP / hostname / 域名
- 看似內部規格的數字(「-73 dBm 因為這是我們 spec」)
- 任何 vendor-specific 控制指令(`iwpriv`、private IOCTL)
- 你公司晶片代號

#### Rule 6:首次 commit 是宣告書

```
Initial commit: Project scope and independence statement

This repository implements an Airtime Fairness (ATF) validation
framework for IEEE 802.11 networks. The framework is platform-agnostic
and tests open-source drivers (mac80211 family) on standard hardware.

Scope and boundaries:
- Tests only IEEE 802.11 standard ATF behavior, using public APIs
  exposed by the Linux mac80211 subsystem (iw, nl80211, debugfs).
- Does not use, reference, or test any vendor-private interfaces.
- Targets only consumer-grade hardware running upstream open-source
  firmware (OpenWrt) and drivers.
- Developed entirely outside of any employer's working hours,
  resources, or networks, on personally-owned equipment.

License: Apache 2.0 (see LICENSE file).

References:
- IEEE 802.11-2020
- Linux mac80211 subsystem documentation
- OpenWrt project
```

#### Rule 7:GitHub Repo 設定

- **Visibility**:Phase 1 / 2 期間 Private,架構穩定 + 律師確認後再轉 Public
- **Owner**:個人帳號,非 organization
- **Branch protection**:main 要求 GPG signed commit + PR

### 15.5 PIIA 與公司政策審查

動工前必做的閱讀清單:

1. **PIIA / Invention Assignment Agreement**(入職時簽的)
   - 確認 §2870 通知條款存在(加州僱主必有)
   - 你的 Prior Inventions Schedule 內容
2. **Open Source Policy**
   - 公司是否要求所有 GitHub 活動用工作 email
   - 是否有 Open Source Committee 審批
   - 個人開源專案是否需報備

### 15.6 加州 vs 台灣的策略差異

**加州不要做的事**(這在台灣可能對,但加州反而傷你):

- ❌ 主動發 email 揭露給 HR/法務(§2870 自動保護,主動揭露反而給公司挑毛病的機會)
- ❌ 把這個專案登記在當前雇主的「ongoing projects」(除非合約明確要求)

**加州正確做法**:

- ✅ 仔細閱讀 PIIA,理解 §2870 自動保護的範圍
- ✅ 物理隔離(設備、網路、時間)做到完全乾淨
- ✅ 個人雲端維護開發日誌(repo 外),記錄環境證據
- ✅ 必要時找律師確認(費用 $500–1500,值得)

### 15.7 絕對紅線(做了直接完蛋)

1. ❌ 從公司 repo / 內部 wiki / Slack 複製任何程式碼或文字
2. ❌ 用公司開發機 / VM / 雲端跑這個專案
3. ❌ 用公司網路 push 到 GitHub
4. ❌ 在公司 Slack / email / 會議討論這個專案技術細節
5. ❌ 測試對象包括「你公司還沒上市的產品」(交叉驗證紅線)
6. ❌ 事後改 commit timestamp 偽造紀錄
7. ❌ 接受同事「我幫你 review」(他們的 review 紀錄會變成爭議點)

第 5 點特別重要:測試對象只能是 upstream 開源 driver + 已上市的 OpenWrt 路由器。即使這個專案完美能勝任公司產品的 ATF 驗證,**也不要這樣用**——這條線一過就不可逆。

### 15.8 個人開發日誌

在 repo **之外**、個人雲端維護 development journal:

```
2026-04-25 (Sat) 21:30-23:45
  - 在家裡 Mac(personal-macbook,序號 XXXX)上開發
  - 用個人 Wi-Fi(SSID: HOME)
  - 無公司 VPN 連線
  - 進度:完成 MQTT topic schema 草稿,push 到個人 GitHub
  - 參考資料:IEEE 802.11-2020、Mosquitto 文件
```

每週 5 分鐘的事,爭議發生時是強有力的時序與環境證據。

### 15.9 Phase 1 必備檔案

repo 根目錄:

```
LICENSE                 # Apache 2.0 全文
NOTICE                  # 第三方 attribution
CONTRIBUTING.md         # 含 DCO
CODE_OF_CONDUCT.md      # Contributor Covenant 範本
README.md               # 第一句:定位錨點
SECURITY.md             # 安全漏洞回報窗口
```

### 15.10 依賴授權審查

每個 dependency 確認與 Apache 2.0 相容:

```
✅ MIT, BSD-2/3, Apache 2.0, ISC, PSF License
⚠️  LGPL(動態連結 OK,看具體用法)
❌ GPL v2/v3, AGPL(會傳染)
```

工具:
```bash
pip-licenses --format=markdown --output-file=docs/dependency-licenses.md
```

把報告 commit 進 repo,定期更新——「我認真審查過授權相容性」的證據。

---

## 16. 動工前檢查清單

```
法律準備
□ 找出並仔細閱讀 PIIA / Invention Assignment Agreement
□ 確認 §2870 通知條款存在
□ 確認你的 Prior Inventions Schedule 內容
□ 找出並閱讀公司 Open Source Policy
□ (建議)諮詢加州智財律師 1–2 小時
□ 個人雲端開發日誌檔案已建立

技術環境隔離
□ 100% 個人擁有的開發設備(無公司 MDM)
□ 100% 個人網路(家用或個人手機熱點)
□ 個人 Apple ID / Google account 登入
□ 個人 GitHub 帳號,SSH key 與工作完全分離
□ 不使用任何公司提供的雲端服務

Git / Repo 設定
□ Repo 設為 Private
□ Git config:user.email / user.name 設在 repo 內
□ GPG key 產生並設定 commit signing
□ Apache 2.0 LICENSE 檔已就位
□ NOTICE 檔已就位
□ README 第一句寫好定位錨點
□ Initial commit 草稿寫好(獨立宣言格式)
□ CONTRIBUTING.md 含 DCO 規則
□ Source file license header 規範定好

時間紀律
□ Commit 時段限定:工餘時間、週末、假日
□ 不在公司網路 / 公司設備上做任何 commit / push

技術準備
□ OpenWrt AP 型號確定 → hostapd 設定可寫
□ RPi 採購到位
□ 個人 Mac docker 可運作
□ 開發環境(Python 3.11+, uv/poetry)就緒
```

---

## 17. 後續 Phase 預覽

### Phase 2:跨平台驗證

- 加入 macOS、Windows、Android(Termux)agent
- 平台抽象層落實:`MacosAdapter` / `WindowsAdapter` / `AndroidAdapter`
- 每平台 1–2 台 STA,驗證 metric 一致性
- 規模仍維持 5–10 台

**關鍵設計**:Phase 1 已預留 `PlatformAdapter` 介面,Phase 2 只新增實作,不改架構。

### Phase 3:規模化

- 逐步擴到 20 → 50 台,每批量同步精度
- 硬體混合配置:RPi Zero 2 W + Android 手機 + Mini PC
- Broker tuning(`max_inflight_messages` / 持久化策略)
- 可能需要 RF 隔離環境
- 分組測試策略

### Phase 4:文件 / 開源

- 完整 scenario 庫
- 方法學論文 / blog post
- GitHub 轉 Public(在律師確認、文件齊全後)
- 加 MQTT auth + TLS
- DMCA counter-notice SOP 準備

---

## 18. 未解決事項

Phase 1 動工前需處理:

1. **OpenWrt AP 具體型號** → 影響 hostapd 設定、ATF 控制方式
2. **PIIA / 公司 Open Source Policy** 自我審查
3. **(建議)律師諮詢一次**

Phase 1 內可動態決定:

- iperf3 多 stream aggregate 策略(先單 stream)
- AP 端 debugfs 具體可讀欄位(視 driver 而定)
- Wi-Fi `noise_dbm` 在不同 driver 是否可得(agent capability collector 偵測後填 null)

未來 Phase 處理:

- MQTT auth / TLS(Phase 4)
- Payload 壓縮(Phase 3 若需要)
- 真正開源 / 律師最終審查(Phase 4 上 Public 前)

---

## 附錄 A:術語表

| 術語 | 定義 |
|---|---|
| ATF | Airtime Fairness,IEEE 802.11 標準的時間公平性機制 |
| STA | Station,Wi-Fi 客戶端 |
| AP | Access Point,Wi-Fi 接取點 |
| DUT | Device Under Test,被測裝置 |
| Jain's Fairness Index | 公平性量化指標,範圍 0–1,越接近 1 越公平 |
| PIIA | Proprietary Information and Inventions Agreement |
| LWT | Last Will and Testament(MQTT 斷線遺言) |
| DCO | Developer Certificate of Origin |
| MCS | Modulation and Coding Scheme |
| SSE | Server-Sent Events |

## 附錄 B:參考資料

- IEEE 802.11-2020 Standard
- Linux mac80211 documentation: https://wireless.wiki.kernel.org/en/developers/documentation/mac80211
- OpenWrt project: https://openwrt.org
- iperf3: https://iperf.fr
- MQTT v3.1.1 specification: https://docs.oasis-open.org/mqtt/
- California Labor Code §2870: https://leginfo.legislature.ca.gov
- Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0
- Developer Certificate of Origin: https://developercertificate.org

---

**文件結束**

下一步建議:看完整份規格後,選擇開始展開哪個技術項目(Pydantic models / Agent 骨架 / Orchestrator 主流程 / OpenWrt 設定)。
