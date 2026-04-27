# 多平台支援 — 路線圖與架構

syncbench 的 agent 設計支援異質客戶端裝置，反映真實世界的 Wi-Fi 群體（Android 手機 + Linux 筆電 + IoT 板，共用一個 AP）。本文說明目前支援矩陣、抽象層、以及如何加入新平台。

> English: [multi-platform.md](multi-platform.md)

---

## 支援矩陣

| 平台 | 狀態 | Adapter | 已測試 | 備註 |
|---|---|---|---|---|
| Linux（Debian-based）| ✅ Stable | `LinuxAdapter` | RPi 4/400/500、Ubuntu/Debian 筆電 | Phase 1 參考平台 |
| macOS（Apple Silicon）| 🟡 Dev only | `MacOSAdapter` | Mac mini M 系列 | 用作 controller；agent 路徑可跑 local smoke test |
| Windows | ⚪ 規劃中（Phase 2）| — | — | `netsh wlan` 取 link，`w32time` 取 NTP |
| Android | ⚪ 規劃中（Phase 2）| — | — | Termux + iperf3 binary，`dumpsys wifi` 取 link |
| iOS | ⚪ 未來（Phase 3）| — | — | 需要原生 app（沒 shell）|
| FreeBSD / OpenBSD | ⚪ 未來 | — | — | `ifconfig`、`ntpd` — LinuxAdapter 直接移植即可 |

**Stable** = 實際測試環境跑過，所有功能可用。
**Dev only** = 程式碼路徑存在，但沒在真實 testbed 操作。
**Planned** = 透過 `PlatformAdapter` ABC 預留位置，尚未實作。

---

## 架構：PlatformAdapter

所有 OS 專屬邏輯都隔離在一個 ABC：`agent/atf_agent/platform/base.py`。

```python
class PlatformAdapter(ABC):
    def get_platform_info(self) -> PlatformInfo
    def get_wifi_interface(self) -> str | None
    def get_wifi_mac(self) -> str | None
    def get_link_info(self) -> LinkInfo
    def get_ntp_offset_ms(self) -> float | None
    def is_ntp_synced(self) -> bool
```

Agent 在啟動時依 `platform.system()` 選擇 adapter：

```python
def _make_platform_adapter():
    os_name = platform.system()
    if os_name == "Linux":   return LinuxAdapter()
    if os_name == "Darwin":  return MacOSAdapter()
    raise RuntimeError(f"Unsupported platform: {os_name}")
```

這層之上的所有東西（狀態機、MQTT bus、iperf3 runner、sync、orchestrator protocol）都是平台無關的，**加新平台不需要改動**。

---

## 加入新平台的步驟

以加入 Windows 為例：

1. **實作 `WindowsAdapter`** 在 `agent/atf_agent/platform/windows.py`：
   - `get_wifi_interface()` — parse `netsh wlan show interfaces`
   - `get_wifi_mac()` — parse `getmac` 或 `wmic nic`
   - `get_link_info()` — `netsh wlan show interfaces`（SSID、BSSID、訊號）
   - `get_ntp_offset_ms()` — `w32tm /query /status`
   - `is_ntp_synced()` — 同上

2. **接上 adapter** 在 `_make_platform_adapter()`：
   ```python
   if os_name == "Windows": return WindowsAdapter()
   ```

3. **驗證 iperf3 可用**：
   - Windows：`choco install iperf3` 或用 bundled exe
   - 確認 `iperf3 --version` 在 PowerShell 跑得起來

4. **加 setup script** 在 `scripts/setup-windows.ps1`，比照 Linux flow（chocolatey 安裝、scheduled task 取代 systemd 等）

5. **跑 smoke test**：
   ```bash
   atf-run scenarios/00_smoke_test.yaml
   ```
   從 controller 端跑，新平台裝置加入測試 SSID。

6. **更新本文件** — 把狀態從 Planned 升到 Dev、再升到 Stable。

---

## 各平台注意事項

### Linux（Raspberry Pi）

- RPi OS Lite 預設用 `wpa_supplicant`
- 用 `hostnamectl set-hostname` 改 hostname
- iperf3 ≥ 3.6 才有 `--forceflush`（即時串流必備）

### Linux（筆電 / 桌機）

- Ubuntu/Fedora/Arch 預設用 `NetworkManager` — 用 `nmcli` 不要用 `wpa_supplicant`
- **必須關掉 Wi-Fi power save**（筆電會積極省電 Wi-Fi 模組）：
  ```bash
  sudo iw dev wlan0 set power_save off
  # 透過 NetworkManager 持久化：
  sudo nmcli connection modify <name> 802-11-wireless.powersave 2
  ```
- **長時間測試要關掉 suspend/hibernate**：
  ```bash
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
  ```
- 背景應用程式（瀏覽器、Slack、Spotify）會產生不可控的 Wi-Fi 流量 — 關掉它們，或用專屬測試 user 跑 agent

### macOS

- `airport` CLI 是唯一不彈 GUI 就能讀 SSID/BSSID 的方式（macOS 14+ 已 deprecated；可能需 `wdutil` fallback）
- NTP 由 `timed` 管，沒 chrony — `is_ntp_synced()` 永遠回 True（controller 端用沒問題）
- 沒法不透過 GUI 關 Wi-Fi power save — 不建議當作 STA 跑生產測試

### Windows（規劃中）

- Wi-Fi info 透過 `netsh wlan show interfaces`
- NTP 透過 `w32time`，但 offset 解析度粗（秒級，不是毫秒）
- iperf3 必須在 PATH；建議用 chocolatey 或 scoop 安裝
- systemd → Task Scheduler 用 `New-ScheduledTask` 自動啟動

### Android（規劃中）

- Termux 提供接近 Linux 的環境；`iperf3` 跟 `iw` 對應指令（`tcpdump`、`dumpsys wifi`）可用
- 沒 systemd — 用 Termux:Boot 自動啟動
- 電池管理會殺背景 app — 必須允許「忽略電池最佳化」
- mDNS 可能需要 workaround（Android 不一定接受非系統 app 的 `.local`）

---

## 混合平台測試 scenario

當多個平台都 stable 後，單一 scenario YAML 可以混搭：

```yaml
extends: _base/normal.yaml
name: "異質 5-STA 混合測試"
duration_sec: 60

stations:
  - node: rpi-sta-01
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
  - node: rpi-sta-02
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
  - node: linux-nb-01      # 筆電
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
  - node: android-pixel-01 # 手機 via Termux agent
    traffic: { type: iperf3_udp, server: "atf-broker.local", bandwidth_mbps: 30 }
  - node: windows-nb-01    # Windows 筆電
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
```

Orchestrator 完全不在乎 agent 的 OS — 只關心 agent 能正確回應 `prepare`/`start_at`/`stop` MQTT 指令。這就是平台無關抽象的價值。

---

## `--agent-id` 命名慣例

| 樣式 | 範例 | 用於 |
|---|---|---|
| `rpi-sta-NN` | `rpi-sta-01` | Raspberry Pi |
| `linux-nb-NN` | `linux-nb-01` | Linux 筆電 |
| `linux-pc-NN` | `linux-pc-01` | Linux 桌機 / NUC |
| `android-MODEL-NN` | `android-pixel-01` | Android 手機（型號縮寫）|
| `win-nb-NN` | `win-nb-01` | Windows 筆電 |
| `mac-mini-NN` | `mac-mini-01` | Mac mini（少見；通常只當 controller）|

`atf-ap-collector` 透過 MQTT retained status 自動學 MAC↔agent_id 對應，**不論命名規則為何都不需要 static config**。
