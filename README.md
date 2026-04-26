# ATF Validator

A cross-platform, open-source Wi-Fi Airtime Fairness (ATF) validation framework for IEEE 802.11 networks, testing open-source drivers (mac80211 family) on standard consumer hardware.

## Overview

This framework validates IEEE 802.11 standard ATF behavior across multiple stations (STA) using only public APIs: `iw`, `nl80211`, `hostapd_cli`, and Linux `debugfs`. No vendor-private interfaces are used or referenced.

**Test targets:** Consumer-grade hardware running upstream open-source firmware (OpenWrt) with standard drivers (ath9k, ath10k, mt76).

## Architecture

- **Controller** (Mac): Orchestrates test scenarios via MQTT broadcast
- **Agent** (Raspberry Pi STA): Runs iperf3, reports metrics at 1 Hz
- **AP Collector** (OpenWrt): Reads debugfs airtime stats
- **Inspector UI**: Real-time environment status dashboard
- **InfluxDB + Grafana**: Time-series storage and visualization

## Phase 1 Goals

1. Single-command execution: `atf-run scenarios/01_two_sta_equal.yaml`
2. Real-time visualization: per-STA throughput curves in Grafana
3. Automated report: Jain's Fairness Index, per-STA throughput, retry rate
4. Sync precision: STA start-time jitter measured < 100 ms
5. Reproducibility: re-runnable within 5 minutes of system restart

## Getting Started

See [docs/development-setup.md](docs/development-setup.md) for full prerequisites and setup instructions.

**Quick summary of required tools:**
- Homebrew + gnupg + pinentry-mac
- SSH key + GPG key (configured in GitHub)
- uv (Python package manager) + Python 3.11
- Docker Desktop

## License

Apache 2.0 — see [LICENSE](LICENSE)

## References

- IEEE 802.11-2020
- [Linux mac80211 subsystem](https://wireless.wiki.kernel.org/en/developers/documentation/mac80211)
- [OpenWrt](https://openwrt.org)
- [iperf3](https://iperf.fr)
