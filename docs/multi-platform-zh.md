# 多平台支援 — 路線圖與架構

syncbench 的 agent 設計支援異質客戶端裝置，反映真實世界的 Wi-Fi 群體（Android 手機 + Linux 筆電 + IoT 板，共用一個 AP）。本文說明目前支援矩陣、抽象層、以及如何加入新平台。

> English: [multi-platform.md](multi-platform.md)

---

## 支援矩陣

| 平台 | 狀態 | Adapter | 已測試 | 備註 |
|---|---|---|---|---|
| Linux（Debian-based）| ✅ Stable | `LinuxAdapter` | RPi 4/400/500、Ubuntu/Debian 筆電 | Phase 1 參考平台 |
| macOS（Apple Silicon）| ✅ Stable | `MacOSAdapter` | Mac mini M 系列、MacBook (macOS 26+) | 用 `scripts/setup-macos.sh`；LaunchAgent 自動啟動 |
| Windows | 🟡 Dev only | `WindowsAdapter` | _尚未實機驗證_（目標英文 UI）| 用 `scripts/setup-windows.ps1`；手動 launcher（無自動啟動）|
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

以加入 Android 為例（Windows 已實作，可作為新平台 onboarding 範本）：

1. **實作 `AndroidAdapter`** 在 `agent/atf_agent/platform/android.py`：
   - `get_wifi_interface()` — parse `ip link` 或 `dumpsys wifi`
   - `get_wifi_mac()` — parse `cat /sys/class/net/wlan0/address` 或 `dumpsys wifi`
   - `get_link_info()` — `dumpsys wifi`（SSID、BSSID、RSSI、frequency）
   - `get_ntp_offset_ms()` — Android 無 `chrony` / `w32tm`，需考慮 system clock 同步策略
   - `is_ntp_synced()` — 同上

2. **接上 adapter** 在 `_make_platform_adapter()`：
   ```python
   if os_name == "Linux" and "android" in platform.release().lower():
       return AndroidAdapter()
   ```

3. **驗證 iperf3 可用**：
   - Termux：`pkg install iperf3`
   - 確認 `iperf3 --version` 在 Termux 跑得起來

4. **加 setup script** 在 `scripts/setup-android.sh`，比照 Linux flow（Termux 套件管理、Termux:Boot 自動啟動取代 systemd 等）

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

- `airport` CLI 在 macOS 14.4+ 已被 Apple 移除（macOS 26 完全沒了）。Link info 改用 `system_profiler -json SPAirPortDataType`，會強制 Wi-Fi 重掃所以單次 ~7-8s — `MacOSAdapter` 把它放到背景 daemon thread 跑，heartbeat 永遠拿 cache 秒回（每 30s 刷新）
- IP 用 `ipconfig getifaddr <iface>`（cheap，每次 heartbeat 都呼叫沒問題）
- SSID/BSSID 在沒給 Terminal/Python Location Services 權限的情況下會被 Apple 隱碼成 `<redacted>`（System Settings → Privacy & Security → Location Services 可解）。Channel/RSSI/PHY rate 不受影響，`band` 跟 `freq_mhz` 可靠
- 6E channel 1 = 5955 MHz 落在 base.py 6000 邊界以下，`MacOSAdapter.get_band()` 把邊界改成 5925 MHz
- NTP 由 `timed` 管，沒 chrony — `is_ntp_synced()` 永遠回 True
- 沒法程式化關 Wi-Fi power save — 穩定吞吐記得插電；長時間測試考慮 `defaults write NSGlobalDomain NSAppSleepDisabled -bool YES` 關 App Nap
- 自動啟動透過 `~/Library/LaunchAgents/com.atf.agent.plist`（`scripts/setup-macos.sh` 自動建好）

### Windows

> ⚠ **狀態：程式碼完成，尚未實機驗證。** Adapter + setup scripts + 文件透過多 agent dev flow 完成；functional 與 security review 都過（0 P0）。`winget` iperf3 套件 id、`netsh` 實機輸出欄位、混合平台 scenario 跑通—這些都還需要在 Windows 機器上驗證。若 parser 在實機上漂移，請附 `netsh wlan show interfaces` 完整輸出開 issue。

- Wi-Fi info 透過 `netsh wlan show interfaces`（~50 ms 同步呼叫，不像 macOS `system_profiler` 需要背景 poll thread）
- **MVP 限英文 Windows UI** — 非英文版本 `netsh` 欄位標籤會被在地化（例如德文「Signal」、日文 / 繁中），會打壞 adapter 的解析。非英文支援列為已知 caveat
- **訊號是百分比，不是 dBm** — adapter 用 Microsoft 公式估算 `RSSI_dBm ≈ (signal_pct / 2) - 100`。要絕對 dBm 請外接 sniffer
- **防火牆規則由 `scripts/setup-windows.ps1` 預先建好**（admin 一次性）：iperf3 TCP/UDP port 5201，限縮為 `-Profile Domain,Private -RemoteAddress LocalSubnet`（公共 Wi-Fi 上 port 不暴露，且只接收同網段流量）。Setup 必須 Administrator 跑；launcher（`run-agent.ps1`）不需要 admin
- **不自動啟動** — 使用者每次測試手動跑 `scripts/run-agent.ps1`（仿 macOS LaunchAgent 「setup once」精神但不綁定自動啟動；Windows Task Scheduler 刻意不用，讓 agent 在 Task Manager 中可見）
- **Wi-Fi adapter 睡眠**會影響長時間測試。建議：High-Performance 電源計畫（`powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`）+ 關 USB selective suspend
- **套件管理：winget**（Windows 10 1809+/11 內建）。iperf3 winget id 待實機確認（`ar51an.iperf3-win-builds` 或 `iPerf.iPerf3`），手動安裝 fallback 寫在 `setup-windows.ps1`
- **NTP 用 `w32time`** — 全新 Win10/11 桌面版的 `w32time` 可能是 manual trigger；`is_ntp_synced()` 在第一次同步前回 False。要嚴格時間同步請手動跑 `w32tm /resync` 或 `Start-Service w32time`

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
