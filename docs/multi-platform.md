# Multi-Platform Support — Roadmap & Architecture

syncbench agents run on heterogeneous client devices to mirror real-world Wi-Fi populations (Android phone + Linux laptop + IoT board, all sharing one AP). This document describes the current support matrix, the abstraction layer, and how to add a new platform.

> 中文版：[multi-platform-zh.md](multi-platform-zh.md)

---

## Support Matrix

| Platform | Status | Adapter | Tested on | Notes |
|---|---|---|---|---|
| Linux (Debian-based) | ✅ Stable | `LinuxAdapter` | RPi 4/400/500, Ubuntu/Debian laptops | Phase 1 reference platform |
| macOS (Apple Silicon) | 🟡 Dev only | `MacOSAdapter` | Mac mini M-series | Used as controller; agent works for local smoke tests |
| Windows | ⚪ Planned (Phase 2) | — | — | `netsh wlan` for link info, `w32time` for NTP |
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

To add (e.g.) Windows support:

1. **Implement `WindowsAdapter`** under `agent/atf_agent/platform/windows.py`:
   - `get_wifi_interface()` — parse `netsh wlan show interfaces`
   - `get_wifi_mac()` — parse `getmac` or `wmic nic`
   - `get_link_info()` — `netsh wlan show interfaces` (SSID, BSSID, signal)
   - `get_ntp_offset_ms()` — `w32tm /query /status`
   - `is_ntp_synced()` — same

2. **Wire the adapter** in `_make_platform_adapter()`:
   ```python
   if os_name == "Windows": return WindowsAdapter()
   ```

3. **Verify iperf3 works**:
   - Windows: install via `choco install iperf3` or use bundled exe
   - Confirm `iperf3 --version` runs from PowerShell

4. **Add a setup script** under `scripts/setup-windows.ps1` mirroring the Linux flow (chocolatey install, scheduled task instead of systemd, etc.)

5. **Test the smoke scenario**:
   ```bash
   atf-run scenarios/00_smoke_test.yaml
   ```
   from the controller, with the new platform device joined to the test SSID.

6. **Update this doc** — bump status from Planned to Dev to Stable as confidence grows.

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

- `airport` CLI is the only way to read SSID/BSSID without GUI prompts (deprecated in macOS 14+; may need `wdutil` fallback)
- NTP managed by `timed`, no chrony — `is_ntp_synced()` returns True unconditionally (acceptable for controller-side use)
- Cannot disable Wi-Fi power save without GUI changes — not recommended as a STA in production tests

### Windows (planned)

- Wi-Fi info via `netsh wlan show interfaces`
- NTP via `w32time`, but offset reporting is coarse (seconds, not ms)
- iperf3 must be on PATH; recommend chocolatey or scoop install
- systemd → Task Scheduler with `New-ScheduledTask` for auto-start

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
