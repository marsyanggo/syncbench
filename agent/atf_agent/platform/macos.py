import json
import logging
import platform
import re
import subprocess
import threading
import time

from .base import LinkInfo, PlatformAdapter, PlatformInfo

logger = logging.getLogger(__name__)

# system_profiler triggers a fresh Wi-Fi scan and takes ~7-8s on macOS 14+.
# Run it on a background thread so heartbeat never blocks; refresh at this
# cadence (link info changes rarely during a test run).
_LINK_POLL_INTERVAL_SEC = 30.0
_SYSTEM_PROFILER_TIMEOUT_SEC = 15.0


class MacOSAdapter(PlatformAdapter):
    """macOS implementation — agent path for Mac STA / dev testing.

    Tested on macOS 26.x. Apple removed the legacy `airport` CLI, so link
    info comes from `system_profiler -json SPAirPortDataType`. That call is
    slow (~7s, full Wi-Fi rescan) so it runs on a background daemon thread;
    `get_link_info()` always returns the last cached value instantly.

    IP comes from `ipconfig getifaddr` — fast enough to call per heartbeat.
    """

    def __init__(self) -> None:
        self._iface_cache: str | None = None
        self._link_cache: LinkInfo = LinkInfo(connected=False)
        self._link_lock = threading.Lock()
        self._poller_started = False
        self._poller_start_lock = threading.Lock()

    def get_platform_info(self) -> PlatformInfo:
        model = "unknown"
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.model"], text=True, stderr=subprocess.DEVNULL
            )
            model = out.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return PlatformInfo(
            os="macos",
            arch=platform.machine(),
            model=model,
            kernel=platform.release(),
        )

    def get_wifi_interface(self) -> str | None:
        if self._iface_cache:
            return self._iface_cache
        try:
            out = subprocess.check_output(
                ["networksetup", "-listallhardwareports"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            lines = out.splitlines()
            for i, line in enumerate(lines):
                if "Wi-Fi" in line and i + 1 < len(lines):
                    m = re.search(r"Device:\s*(\w+)", lines[i + 1])
                    if m:
                        self._iface_cache = m.group(1)
                        return self._iface_cache
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return None

    def get_wifi_mac(self) -> str | None:
        iface = self.get_wifi_interface()
        if not iface:
            return None
        try:
            out = subprocess.check_output(
                ["ifconfig", iface], text=True, stderr=subprocess.DEVNULL
            )
            m = re.search(r"ether\s+([0-9a-f:]{17})", out)
            return m.group(1).lower() if m else None
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def get_wifi_ip(self) -> str | None:
        # base.py default uses Linux SIOCGIFADDR ioctl (0x8915) which has a
        # different constant on Darwin. Override with `ipconfig getifaddr`.
        iface = self.get_wifi_interface()
        if not iface:
            return None
        try:
            out = subprocess.check_output(
                ["ipconfig", "getifaddr", iface],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return out.strip() or None
        except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def get_link_info(self) -> LinkInfo:
        self._ensure_link_poller()
        with self._link_lock:
            return self._link_cache

    def _ensure_link_poller(self) -> None:
        if self._poller_started:
            return
        with self._poller_start_lock:
            if self._poller_started:
                return
            t = threading.Thread(
                target=self._link_poll_loop, daemon=True, name="macos-link-poll"
            )
            t.start()
            self._poller_started = True

    def _link_poll_loop(self) -> None:
        while True:
            try:
                info = self._read_link_info_uncached()
            except Exception as exc:
                logger.debug("link poll error: %s", exc)
                info = LinkInfo(connected=False)
            with self._link_lock:
                self._link_cache = info
            time.sleep(_LINK_POLL_INTERVAL_SEC)

    def _read_link_info_uncached(self) -> LinkInfo:
        try:
            out = subprocess.check_output(
                ["system_profiler", "-json", "SPAirPortDataType"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=_SYSTEM_PROFILER_TIMEOUT_SEC,
            )
            data = json.loads(out)
        except (subprocess.SubprocessError, FileNotFoundError,
                subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            logger.debug("system_profiler failed: %s", exc)
            return LinkInfo(connected=False)

        try:
            iface_name = self.get_wifi_interface()
            interfaces: list[dict] = []
            for block in data.get("SPAirPortDataType", []):
                interfaces.extend(block.get("spairport_airport_interfaces", []))

            target = next(
                (i for i in interfaces if i.get("_name") == iface_name),
                interfaces[0] if interfaces else None,
            )
            if not target:
                return LinkInfo(connected=False)

            current = target.get("spairport_current_network_information")
            if not current:
                return LinkInfo(connected=False)

            ssid = current.get("_name")
            bssid = current.get("spairport_network_bssid")
            channel_raw = str(current.get("spairport_network_channel", ""))
            signal_raw = str(current.get("spairport_signal_noise", ""))
            rate_raw = current.get("spairport_network_rate")

            # Channel format: "36 (5GHz, 80MHz)" / "1 (6GHz, 160MHz)" / "11 (2GHz, 20MHz)"
            freq_mhz = None
            ch_match = re.match(r"\s*(\d+)\s*\(([\d.]+)\s*GHz", channel_raw)
            if ch_match:
                ch = int(ch_match.group(1))
                ghz = float(ch_match.group(2))
                if ghz >= 6:
                    # 6 GHz channel n → 5950 + n*5 (n=1 → 5955, channel 1 = 5955 MHz)
                    freq_mhz = 5950 + ch * 5
                elif ghz >= 5:
                    freq_mhz = 5000 + ch * 5
                else:
                    freq_mhz = 2407 + ch * 5
            elif (ch_only := re.match(r"\s*(\d+)", channel_raw)):
                ch = int(ch_only.group(1))
                freq_mhz = 5000 + ch * 5 if ch > 14 else 2407 + ch * 5

            rssi_dbm = None
            rssi_match = re.search(r"(-?\d+)\s*dBm", signal_raw)
            if rssi_match:
                rssi_dbm = int(rssi_match.group(1))

            tx_rate = None
            try:
                if rate_raw is not None:
                    tx_rate = float(rate_raw)
            except (TypeError, ValueError):
                pass

            return LinkInfo(
                connected=ssid is not None,
                ssid=ssid,
                bssid=bssid.lower() if bssid else None,
                rssi_dbm=rssi_dbm,
                freq_mhz=freq_mhz,
                tx_rate_mbps=tx_rate,
            )
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.debug("link info parse failed: %s", exc)
            return LinkInfo(connected=False)

    def get_band(self) -> str:
        # 6E channel 1 = 5955 MHz, below the base.py 6000 threshold — would
        # be misclassified as 5G. Override with the correct UNII-5 boundary.
        freq = self.get_link_info().freq_mhz
        if freq is None:
            return "unknown"
        if freq < 3000:
            return "2.4G"
        if freq < 5925:
            return "5G"
        return "6G"

    def get_ntp_offset_ms(self) -> float | None:
        try:
            out = subprocess.check_output(
                ["sntp", "-d", "time.apple.com"],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=3,
            )
            m = re.search(r"([+-][\d.]+)\s*[+-][\d.]+\s*s$", out, re.MULTILINE)
            if m:
                return float(m.group(1)) * 1000
        except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return 0.0  # macOS manages NTP via timed; assume synced

    def is_ntp_synced(self) -> bool:
        return True
