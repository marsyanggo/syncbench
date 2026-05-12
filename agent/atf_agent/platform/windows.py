"""Windows platform adapter for the ATF agent.

Implements PlatformAdapter for Windows 10 / Windows 11 (English UI, MVP).
Wi-Fi info comes from `netsh wlan show interfaces` (~50 ms, no background
poll needed). NTP info comes from `w32tm /query /status`.

STATUS: code complete, NOT YET VALIDATED ON REAL WINDOWS HARDWARE.
The `winget` iperf3 package id and `netsh` field labels may need
adjustment after on-hardware testing. See docs/multi-platform.md and
README.md for the most current Windows support status.
"""

import logging
import platform
import re
import socket
import subprocess

from .base import LinkInfo, PlatformAdapter, PlatformInfo

logger = logging.getLogger(__name__)

_NETSH_TIMEOUT_SEC = 3.0
_W32TM_TIMEOUT_SEC = 3.0
_POWERSHELL_TIMEOUT_SEC = 3.0


class WindowsAdapter(PlatformAdapter):
    """Windows implementation — targets Windows 10/11 English UI.

    `netsh wlan show interfaces` is fast (~50 ms) so no background poll
    thread is needed (unlike macOS). All calls are synchronous.

    Key design decisions:
    - Interface name is cached after first successful lookup (_iface_cache).
    - get_link_info() always calls netsh fresh; the STA may roam during a run.
    - Signal% is converted to RSSI_dBm via the Microsoft formula:
      RSSI_dBm ≈ (signal_pct / 2) - 100.
    - get_wifi_ip() uses a socket trick (no subprocess, no fcntl) since
      base.py's default uses Linux-only fcntl.ioctl (SIOCGIFADDR = 0x8915).
    - get_band() is NOT overridden; base.py derives band from freq_mhz and
      the 6000 MHz boundary is sufficient for Windows (6E channel 1 = 5955
      MHz slight mis-classification is acceptable per spec).
    """

    def __init__(self) -> None:
        self._iface_cache: str | None = None

    # ------------------------------------------------------------------
    # Platform identification
    # ------------------------------------------------------------------

    def get_platform_info(self) -> PlatformInfo:
        """Return OS / arch / hardware model / kernel version."""
        model = "unknown"
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystem).Model"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=_POWERSHELL_TIMEOUT_SEC,
            )
            model = out.strip() or "unknown"
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return PlatformInfo(
            os="windows",
            arch=platform.machine(),
            model=model,
            kernel=platform.release(),
        )

    # ------------------------------------------------------------------
    # Wi-Fi interface
    # ------------------------------------------------------------------

    def get_wifi_interface(self) -> str | None:
        """Return the primary Wi-Fi interface name (typically "Wi-Fi"), or None."""
        if self._iface_cache:
            return self._iface_cache
        out = self._read_netsh()
        if out is None:
            return None
        m = re.search(r"^\s*Name\s*:\s*(.+?)\s*$", out, re.MULTILINE)
        if m:
            self._iface_cache = m.group(1)
            return self._iface_cache
        return None

    # ------------------------------------------------------------------
    # Wi-Fi MAC address
    # ------------------------------------------------------------------

    def get_wifi_mac(self) -> str | None:
        """Return the Wi-Fi MAC address (lowercase, colon-separated), or None."""
        out = self._read_netsh()
        if out is None:
            return None
        m = re.search(
            r"^\s*Physical address\s*:\s*([0-9a-fA-F:\-]{17})\s*$",
            out,
            re.MULTILINE,
        )
        if m:
            mac = m.group(1).lower().replace("-", ":")
            return mac
        return None

    # ------------------------------------------------------------------
    # Wi-Fi link info (main parse logic)
    # ------------------------------------------------------------------

    def get_link_info(self) -> LinkInfo:
        """Parse `netsh wlan show interfaces` and return current link state.

        Returns LinkInfo(connected=False) on any parse or subprocess failure.
        Individual fields (ssid, bssid, etc.) are set to None if absent from
        the output; connected=True is still returned if State=connected.
        """
        out = self._read_netsh()
        if out is None:
            return LinkInfo(connected=False)

        try:
            # Check connection state
            if not re.search(r"^\s*State\s*:\s*connected\s*$", out, re.MULTILINE):
                return LinkInfo(connected=False)

            # SSID — use tight ^\s*SSID\s*: to avoid matching BSSID line
            ssid: str | None = None
            ssid_m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", out, re.MULTILINE)
            if ssid_m:
                ssid = ssid_m.group(1)

            # BSSID
            bssid: str | None = None
            bssid_m = re.search(
                r"^\s*BSSID\s*:\s*([0-9a-fA-F:\-]{17})\s*$", out, re.MULTILINE
            )
            if bssid_m:
                bssid = bssid_m.group(1).lower().replace("-", ":")

            # Channel → freq_mhz
            freq_mhz: int | None = None
            ch_match = re.search(r"^\s*Channel\s*:\s*(\d+)", out, re.MULTILINE)
            band_match = re.search(
                r"^\s*Band\s*:\s*([\d.]+)\s*GHz", out, re.MULTILINE
            )
            if ch_match:
                ch = int(ch_match.group(1))
                if band_match:
                    band_ghz = float(band_match.group(1))
                    if band_ghz >= 6:
                        freq_mhz = 5950 + ch * 5
                    elif band_ghz >= 5:
                        freq_mhz = 5000 + ch * 5
                    else:
                        # 2.4 GHz; channel 14 is a special case (2484 MHz)
                        freq_mhz = 2484 if ch == 14 else 2407 + ch * 5
                else:
                    # Fallback: no Band field (older Windows / Win10)
                    # ch ≤ 14 → 2.4G; otherwise treat as 5G
                    # (6E channel numbers are ambiguous without Band; accept misclassification)
                    if ch <= 14:
                        freq_mhz = 2484 if ch == 14 else 2407 + ch * 5
                    else:
                        freq_mhz = 5000 + ch * 5

            # tx_rate_mbps — use Receive rate (downlink PHY rate, semantically consistent
            # with Linux iw tx bitrate and macOS spairport_network_rate for downlink tests)
            tx_rate_mbps: float | None = None
            rate_m = re.search(
                r"^\s*Receive rate \(Mbps\)\s*:\s*([\d.]+)", out, re.MULTILINE
            )
            if rate_m:
                try:
                    tx_rate_mbps = float(rate_m.group(1))
                except ValueError:
                    pass

            # RSSI: convert Signal% via Microsoft formula: RSSI_dBm ≈ (pct/2) - 100
            rssi_dbm: int | None = None
            sig_m = re.search(r"^\s*Signal\s*:\s*(\d+)%", out, re.MULTILINE)
            if sig_m:
                signal_pct = int(sig_m.group(1))
                rssi_dbm = round((signal_pct / 2) - 100)

            return LinkInfo(
                connected=True,
                ssid=ssid,
                bssid=bssid,
                rssi_dbm=rssi_dbm,
                freq_mhz=freq_mhz,
                tx_rate_mbps=tx_rate_mbps,
            )

        except (ValueError, AttributeError) as exc:
            logger.debug("link info parse failed: %s", exc)
            return LinkInfo(connected=False)

    # ------------------------------------------------------------------
    # Wi-Fi IP address (override base.py — base uses Linux fcntl)
    # ------------------------------------------------------------------

    def get_wifi_ip(self) -> str | None:
        """Return the Wi-Fi interface IPv4 address via socket trick, or None.

        Uses a UDP connect to an external address so the OS selects the source
        IP for the default route — no actual packet is sent. Assumes Wi-Fi is
        the default route (valid for typical laptop-only-Wi-Fi test setups).

        Known limitation: if Ethernet is connected and is the default route,
        the Ethernet IP is returned instead of the Wi-Fi IP.
        """
        iface = self.get_wifi_interface()
        if not iface:
            return None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
            return ip if ip and ip != "0.0.0.0" else None
        except OSError:
            return None

    # ------------------------------------------------------------------
    # NTP offset
    # ------------------------------------------------------------------

    def get_ntp_offset_ms(self) -> float | None:
        """Return NTP clock offset in milliseconds from `w32tm /query /status`.

        Returns None if w32tm is unavailable or times out.
        Returns 0.0 if Phase Offset field is absent (best-effort, like macOS).
        """
        out = self._query_w32tm()
        if out is None:
            return None
        m = re.search(
            r"^\s*Phase Offset\s*:\s*([+-]?[\d.]+)\s*s\s*$", out, re.MULTILINE
        )
        if m:
            try:
                return float(m.group(1)) * 1000.0  # s → ms
            except ValueError:
                pass
        return 0.0

    # ------------------------------------------------------------------
    # NTP sync status
    # ------------------------------------------------------------------

    def is_ntp_synced(self) -> bool:
        """Return True if w32tm reports a real time source synced within 24 h."""
        out = self._query_w32tm()
        if out is None:
            return False

        try:
            # 1. Must have a real Source (not local fallback)
            src_m = re.search(r"^\s*Source\s*:\s*(.+?)\s*$", out, re.MULTILINE)
            has_real_source = bool(
                src_m
                and "Local CMOS Clock" not in src_m.group(1)
                and "Free-running" not in src_m.group(1)
            )

            # 2. Last successful sync must be within 24 hours
            sync_age_m = re.search(
                r"^\s*Time since Last Good Sync Time\s*:\s*([\d.]+)s",
                out,
                re.MULTILINE,
            )
            recently_synced = False
            if sync_age_m:
                age_sec = float(sync_age_m.group(1))
                recently_synced = age_sec < 86400  # 24 h

            return has_real_source and recently_synced

        except (ValueError, AttributeError) as exc:
            logger.debug("w32tm parse failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_netsh(self) -> str | None:
        """Run `netsh wlan show interfaces` and return its stdout, or None on error."""
        try:
            return subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=_NETSH_TIMEOUT_SEC,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.debug("netsh wlan failed: %s", exc)
            return None

    def _query_w32tm(self) -> str | None:
        """Run `w32tm /query /status` and return its stdout, or None on error."""
        try:
            return subprocess.check_output(
                ["w32tm", "/query", "/status"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=_W32TM_TIMEOUT_SEC,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.debug("w32tm failed: %s", exc)
            return None
