import logging
import platform
import re
import subprocess

from .base import LinkInfo, PlatformAdapter, PlatformInfo

logger = logging.getLogger(__name__)


class LinuxAdapter(PlatformAdapter):
    """Linux implementation — targets Raspberry Pi 4 running Raspberry Pi OS."""

    def get_platform_info(self) -> PlatformInfo:
        model = "unknown"
        try:
            with open("/proc/device-tree/model") as f:
                model = f.read().strip("\x00").strip()
        except OSError:
            pass
        return PlatformInfo(
            os="linux",
            arch=platform.machine(),
            model=model,
            kernel=platform.release(),
        )

    def get_wifi_interface(self) -> str | None:
        try:
            out = subprocess.check_output(
                ["iw", "dev"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if "Interface" in line:
                    return line.strip().split()[-1]
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.warning("iw not available")
        return None

    def get_wifi_mac(self) -> str | None:
        iface = self.get_wifi_interface()
        if not iface:
            return None
        try:
            with open(f"/sys/class/net/{iface}/address") as f:
                return f.read().strip().lower()
        except OSError:
            return None

    def get_link_info(self) -> LinkInfo:
        iface = self.get_wifi_interface()
        if not iface:
            return LinkInfo(connected=False)
        try:
            out = subprocess.check_output(
                ["iw", "dev", iface, "link"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return LinkInfo(connected=False)

        if "Not connected" in out:
            return LinkInfo(connected=False)

        def _extract(pattern: str) -> str | None:
            m = re.search(pattern, out)
            return m.group(1) if m else None

        rssi_str = _extract(r"signal:\s*(-\d+)")
        freq_str = _extract(r"freq:\s*(\d+)")
        tx_str = _extract(r"tx bitrate:\s*([\d.]+)")

        return LinkInfo(
            connected=True,
            ssid=_extract(r"SSID:\s*(.+)"),
            bssid=_extract(r"Connected to ([0-9a-f:]{17})"),
            rssi_dbm=int(rssi_str) if rssi_str else None,
            freq_mhz=int(freq_str) if freq_str else None,
            tx_rate_mbps=float(tx_str) if tx_str else None,
        )

    def get_ntp_offset_ms(self) -> float | None:
        # Try chronyc first (preferred on RPi OS), fall back to timedatectl
        try:
            out = subprocess.check_output(
                ["chronyc", "tracking"], text=True, stderr=subprocess.DEVNULL
            )
            m = re.search(r"System time\s*:\s*([\d.]+)\s*seconds", out)
            if m:
                return float(m.group(1)) * 1000
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        try:
            out = subprocess.check_output(
                ["timedatectl", "show", "--property=NTPSynchronized,TimeUSec"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            if "NTPSynchronized=yes" in out:
                return 0.0  # synchronized but offset unknown via this API
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return None

    def is_ntp_synced(self) -> bool:
        try:
            out = subprocess.check_output(
                ["chronyc", "tracking"], text=True, stderr=subprocess.DEVNULL
            )
            return "Reference ID" in out and "0.0.0.0" not in out
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        try:
            out = subprocess.check_output(
                ["timedatectl", "show", "--property=NTPSynchronized"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return "NTPSynchronized=yes" in out
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
