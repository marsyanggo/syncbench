import logging
import platform
import re
import subprocess

from .base import LinkInfo, PlatformAdapter, PlatformInfo

logger = logging.getLogger(__name__)

_AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"


class MacOSAdapter(PlatformAdapter):
    """macOS implementation — used for local development and testing on Mac.

    Wi-Fi info comes from the `airport` utility.
    NTP offset comes from `sntp -d`.
    """

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
                        return m.group(1)
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

    def get_link_info(self) -> LinkInfo:
        try:
            out = subprocess.check_output(
                [_AIRPORT, "-I"], text=True, stderr=subprocess.DEVNULL
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return LinkInfo(connected=False)

        if "AirPort: Off" in out or "state: init" in out:
            return LinkInfo(connected=False)

        def _extract(key: str) -> str | None:
            m = re.search(rf"^\s*{key}:\s*(.+)$", out, re.MULTILINE)
            return m.group(1).strip() if m else None

        ssid = _extract("SSID")
        bssid = _extract("BSSID")
        rssi_str = _extract("agrCtlRSSI")
        channel_str = _extract("channel")

        freq_mhz = None
        if channel_str:
            ch = int(channel_str.split(",")[0])
            freq_mhz = 5000 + ch * 5 if ch > 14 else 2407 + ch * 5

        return LinkInfo(
            connected=ssid is not None,
            ssid=ssid,
            bssid=bssid,
            rssi_dbm=int(rssi_str) if rssi_str else None,
            freq_mhz=freq_mhz,
        )

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
        return 0.0  # assume synced on Mac

    def is_ntp_synced(self) -> bool:
        return True  # macOS manages NTP automatically via timed
