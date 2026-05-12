# Multi-Platform Support — Roadmap & Architecture

syncbench agents run on heterogeneous client devices to mirror real-world Wi-Fi populations (Android phone + Linux laptop + IoT board, all sharing one AP). This document describes the current support matrix, the abstraction layer, and how to add a new platform.

> 中文版：[multi-platform-zh.md](multi-platform-zh.md)

---

## Support Matrix

| Platform | Status | Adapter | Tested on | Notes |
|---|---|---|---|---|
| Linux (Debian-based) | ✅ Stable | `LinuxAdapter` | RPi 4/400/500, Ubuntu/Debian laptops | Phase 1 reference platform |
| macOS (Apple Silicon) | ✅ Stable | `MacOSAdapter` | Mac mini M-series, MacBook (macOS 26+) | Use `scripts/setup-macos.sh`; LaunchAgent auto-start |
| Windows | 🟡 Dev only | `WindowsAdapter` | _Not yet validated on hardware_ (English UI target) | Use `scripts/setup-windows.ps1`; manual launcher (no auto-start) |
| Android | ⚪ Planned (Phase 2) | — | — | Termux + iperf3 binary, `dumpsys wifi` for link info |
| iOS | ⚪ Future (Phase 3) | — | — | Requires native app (no shell) |
| FreeBSD / OpenBSD | ⚪ Future | — | — | `ifconfig`, `ntpd` — straightforward port of LinuxAdapter |

**Stable** = used in production tests, all features work.
**Dev only** = code path exists but not exercised in real testbeds.
**Planned** = scaffolding via `PlatformAdapter` ABC, no implementation yet.

---

## Architecture: PlatformAdapter

All OS-specific logic is isolated behind one ABC: `agent/atf_agent/platform/base.py`.

```python
class PlatformAdapter(ABC):
    def get_platform_info(self) -> PlatformInfo
    def get_wifi_interface(self) -> str | None
    def get_wifi_mac(self) -> str | None
    def get_link_info(self) -> LinkInfo
    def get_ntp_offset_ms(self) -> float | None
    def is_ntp_synced(self) -> bool
```

The agent picks an adapter at startup based on `platform.system()`:

```python
def _make_platform_adapter():
    os_name = platform.system()
    if os_name == "Linux":   return LinuxAdapter()
    if os_name == "Darwin":  return MacOSAdapter()
    raise RuntimeError(f"Unsupported platform: {os_name}")
```

Everything above this layer (state machine, MQTT bus, iperf3 runner, sync, orchestrator protocol) is platform-agnostic and **does not change** when adding a new platform.

---

## Adding a New Platform — Recipe

To add (e.g.) Android support (Windows is now complete — can serve as template):

1. **Implement `AndroidAdapter`** under `agent/atf_agent/platform/android.py`:
   - `get_wifi_interface()` — parse `dumpsys wifi`
   - `get_wifi_mac()` — parse device info
   - `get_link_info()` — `dumpsys wifi` (SSID, BSSID, signal)
   - `get_ntp_offset_ms()` — `ntpctl` (or similar on Termux)
   - `is_ntp_synced()` — same

2. **Wire the adapter** in `_make_platform_adapter()`:
   ```python
   if os_name == "Android": return AndroidAdapter()
   ```

3. **Verify iperf3 works**:
   - Android: install via Termux package manager or binary
   - Confirm `iperf3 --version` runs from shell

4. **Add a setup script** under `scripts/setup-android.sh` mirroring the Linux flow, adapted for Termux environment

5. **Test the smoke scenario**:
   ```bash
   atf-run scenarios/00_smoke_test.yaml
   ```
   from the controller, with the new platform device joined to the test SSID.

6. **Update this doc** — bump status from Planned to Dev to Stable as confidence grows.

> **Example:** Windows (implemented in Phase 2) follows this recipe — see `WindowsAdapter` in `agent/atf_agent/platform/windows.py`, `scripts/setup-windows.ps1`, and the Windows section in Per-Platform Caveats above.

---

## Per-Platform Caveats

### Linux (Raspberry Pi)

- `wpa_supplicant` is the default Wi-Fi stack on RPi OS Lite
- Hostname via `hostnamectl set-hostname`
- iperf3 ≥ 3.6 required for `--forceflush` (real-time streaming)

### Linux (laptop / desktop)

- `NetworkManager` is the default on Ubuntu/Fedora/Arch — use `nmcli` not `wpa_supplicant`
- **Disable Wi-Fi power save** (laptops aggressively power-save the radio):
  ```bash
  sudo iw dev wlan0 set power_save off
  # Persist via NetworkManager:
  sudo nmcli connection modify <name> 802-11-wireless.powersave 2
  ```
- **Disable suspend/hibernate** during long test runs:
  ```bash
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
  ```
- Background apps (browser, Slack, Spotify) generate uncontrolled Wi-Fi traffic — close them or run the agent under a dedicated test user

### macOS

- `airport` CLI removed in macOS 14.4+ (and absent on macOS 26). Link info comes from `system_profiler -json SPAirPortDataType`, which forces a fresh Wi-Fi rescan and takes ~7-8s per call — `MacOSAdapter` runs it on a background daemon thread and the heartbeat returns the cached value instantly (refreshed every 30s)
- IP via `ipconfig getifaddr <iface>` (cheap, called per heartbeat)
- SSID/BSSID are redacted by Apple privacy unless Terminal/Python is granted Location Services permission (System Settings → Privacy → Location Services). Channel/RSSI/PHY rate work without permission — `band` and `freq_mhz` are reliable
- 6E channel 1 = 5955 MHz, below the base.py 6000 threshold, so `MacOSAdapter.get_band()` overrides the boundary at 5925 MHz
- NTP managed by `timed`, no chrony — `is_ntp_synced()` returns True unconditionally
- Cannot disable Wi-Fi power save programmatically — keep the Mac plugged in for stable throughput; consider `defaults write NSGlobalDomain NSAppSleepDisabled -bool YES` to suppress App Nap
- Auto-start via LaunchAgent at `~/Library/LaunchAgents/com.atf.agent.plist` (installed by `scripts/setup-macos.sh`)

### Windows

> ⚠ **Status: code complete, not yet validated on real Windows hardware.** Adapter + setup scripts + docs landed via a multi-agent dev flow; both functional and security reviews passed (0 P0). On-hardware verification of `winget` iperf3 package id, `netsh` field labels on real Windows output, and a mixed-OS scenario run is pending. Report issues with concrete output samples if you hit parser drift.

- Wi-Fi info via `netsh wlan show interfaces` (~50 ms, no background poll needed unlike macOS)
- **English Windows UI required for MVP** — non-English builds report localized field names (e.g. German "Signal" → "Signal"). Adapter parses English labels; non-English UI is a documented caveat
- **Signal strength is a percentage**, not actual dBm. Adapter estimates `RSSI_dBm = (signal_pct / 2) - 100` (Microsoft formula). Treat as approximate; for absolute RSSI use an external sniffer
- **Firewall rule pre-added** by `scripts/setup-windows.ps1` (admin one-shot) for iperf3 TCP/UDP port 5201, scoped to `-Profile Domain,Private -RemoteAddress LocalSubnet` (port stays closed on public Wi-Fi like coffee-shop/airport, and only accepts inbound from the local subnet). The setup script must be run as Administrator; the launcher (`run-agent.ps1`) does not need admin
- **No auto-start** — user manually runs `scripts/run-agent.ps1` per test session (mirror macOS LaunchAgent's "setup once, user launches" philosophy but without the auto-start binding; Windows Task Scheduler is intentionally not used to keep the agent obvious in Task Manager)
- **Wi-Fi adapter sleep** can affect long runs. Recommendation: set High-Performance power plan (`powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`) and disable USB selective suspend
- **Package manager: winget** (built into Windows 10 1809+ and Windows 11). iperf3 winget id (TODO: verify) — `ar51an.iperf3-win-builds` or `iPerf.iPerf3`; manual install fallback documented in `setup-windows.ps1`
- NTP via `w32time`. Default Win10/11 desktop installs may have `w32time` as manual trigger; `is_ntp_synced()` returns False until first sync. If clock sync is critical, run `w32tm /resync` or `Start-Service w32time` once

### Android (planned)

- Termux provides a near-Linux environment; `iperf3` and `iw`-equivalent (`tcpdump`, `dumpsys wifi`) work
- No systemd — use Termux:Boot for auto-start
- Battery management will kill background apps — must be granted "ignore battery optimization"
- mDNS may require a workaround (Android does not always honor `.local` from non-system apps)

---

## Mixed-Platform Test Scenarios

Once multiple platforms are stable, a single scenario YAML can mix them:

```yaml
extends: _base/normal.yaml
name: "Heterogeneous 5-STA mix"
duration_sec: 60

stations:
  - node: rpi-sta-01
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
  - node: rpi-sta-02
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
  - node: linux-nb-01      # laptop
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
  - node: android-pixel-01 # phone via Termux agent
    traffic: { type: iperf3_udp, server: "atf-broker.local", bandwidth_mbps: 30 }
  - node: windows-nb-01    # Windows laptop
    traffic: { type: iperf3_tcp, server: "atf-broker.local" }
```

The orchestrator does not care about agent OS — it only cares that the agent answers `prepare`/`start_at`/`stop` MQTT commands correctly. This is the value of the platform-agnostic abstraction.

---

## Naming Conventions for `--agent-id`

| Pattern | Example | Use for |
|---|---|---|
| `rpi-sta-NN` | `rpi-sta-01` | Raspberry Pi |
| `linux-nb-NN` | `linux-nb-01` | Linux laptop |
| `linux-pc-NN` | `linux-pc-01` | Linux desktop / NUC |
| `android-MODEL-NN` | `android-pixel-01` | Android phone (model abbreviated) |
| `win-nb-NN` | `win-nb-01` | Windows laptop |
| `mac-mini-NN` | `mac-mini-01` | Mac mini (rare; usually controller-only) |

`atf-ap-collector` learns the MAC ↔ agent_id mapping automatically from MQTT retained status — no static config needed regardless of naming scheme.
