from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LinkInfo:
    connected: bool
    ssid: str | None = None
    bssid: str | None = None
    rssi_dbm: int | None = None
    freq_mhz: int | None = None
    tx_rate_mbps: float | None = None


@dataclass
class PlatformInfo:
    os: str          # "linux" | "macos" | "windows" | "android"
    arch: str        # "arm64" | "x86_64"
    model: str       # "Raspberry Pi 4" | "Mac mini" | ...
    kernel: str


class PlatformAdapter(ABC):
    """Abstract interface for OS-specific operations.

    Phase 1 implements LinuxAdapter (RPi) and MacOSAdapter (dev testing).
    Phase 2 will add WindowsAdapter and AndroidAdapter without changing
    this interface.
    """

    @abstractmethod
    def get_platform_info(self) -> PlatformInfo: ...

    @abstractmethod
    def get_wifi_interface(self) -> str | None:
        """Return the primary Wi-Fi interface name, or None if not found."""
        ...

    @abstractmethod
    def get_link_info(self) -> LinkInfo:
        """Return current Wi-Fi link state."""
        ...

    @abstractmethod
    def get_wifi_mac(self) -> str | None:
        """Return the Wi-Fi interface's MAC address (lowercase, colon-separated)."""
        ...

    @abstractmethod
    def get_ntp_offset_ms(self) -> float | None:
        """Return current NTP clock offset in milliseconds, or None if unknown."""
        ...

    @abstractmethod
    def is_ntp_synced(self) -> bool:
        """Return True if the system clock is NTP-synchronized."""
        ...

    def get_band(self) -> str:
        """Return connected Wi-Fi band: '2.4G', '5G', '6G', or 'unknown'.

        Derived from get_link_info().freq_mhz — no per-platform override needed
        unless the adapter cannot populate freq_mhz.
        """
        freq = self.get_link_info().freq_mhz
        if freq is None:
            return "unknown"
        if freq < 3000:
            return "2.4G"
        if freq < 6000:
            return "5G"
        return "6G"
