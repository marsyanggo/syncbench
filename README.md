# syncbench

A cross-platform, open-source framework for synchronized multi-client network performance benchmarking. Coordinates heterogeneous client devices (Raspberry Pi, Linux laptops, etc.) to run simultaneous iperf3 traffic with sub-millisecond start-time synchronization, visualizes results in real-time on Grafana, and computes Jain's Fairness Index automatically.

Originally designed to validate Wi-Fi Airtime Fairness (ATF) on IEEE 802.11 networks — but the orchestration pipeline is transport-agnostic and works for any synchronized multi-endpoint traffic scenario.

## Overview

- **MQTT-orchestrated**: controller broadcasts synchronized start timestamps to all agents
- **Sub-millisecond sync**: NTP-aware `sleep_until` (coarse sleep + busy-wait), measured 0–1 ms across ARM64/x86_64
- **Cross-platform agents**: Raspberry Pi OS, Ubuntu/Debian laptops — same codebase via `PlatformAdapter` ABC
- **Auto iperf3 management**: server processes spawned/killed per run, unique port per STA
- **Real-time Grafana**: per-second throughput curves appear live during the test
- **Auto report**: Jain's Fairness Index + markdown report generated on every run

Uses only public kernel interfaces: `iw`, `nl80211`, `hostapd_cli`, Linux `debugfs`. No vendor-private APIs.

## Phase 1 Results (2 RPi + 1 Linux NB)

| Scenario | Jain's FI | Sync Offset |
|---|---|---|
| 2-STA homogeneous (RPi×2) | **0.999** | 0 ms |
| 3-STA heterogeneous (RPi×2 + NB Wi-Fi 6) | **0.642** | 0–1 ms |

Note: AX4200 (MT7986A/mt76) ATF is not effective in HE80 OFDMA mode — see [methodology](docs/methodology.md).

## Quick Start

```bash
# Start infrastructure
docker compose up -d

# Run a test (auto-spawns iperf3 servers, syncs agents, writes Grafana, generates report)
uv run atf-run scenarios/01_two_sta_equal.yaml

# View results
open http://localhost:3000   # Grafana
open http://localhost:8080   # Inspector (live agent status)
```

## Documentation

- **User guide:** [English](docs/user-guide-en.md) / [中文](docs/user-guide-zh.md)
- **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Methodology + ATF findings:** [docs/methodology.md](docs/methodology.md)
- **Multi-platform roadmap:** [docs/multi-platform.md](docs/multi-platform.md) / [中文](docs/multi-platform-zh.md)
- **Dev setup:** [docs/development-setup.md](docs/development-setup.md)

## License

Apache 2.0 — see [LICENSE](LICENSE)

## References

- IEEE 802.11-2020
- [Linux mac80211](https://wireless.wiki.kernel.org/en/developers/documentation/mac80211)
- [OpenWrt](https://openwrt.org) · [iperf3](https://iperf.fr) · [mt76](https://github.com/openwrt/mt76)
