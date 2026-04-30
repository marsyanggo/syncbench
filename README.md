# syncbench

**A synchronized multi-endpoint benchmark orchestrator for heterogeneous testbeds.**

Coordinates real client devices — Raspberry Pi, Linux laptops, Android phones, and beyond — to launch traffic at the **same wall-clock instant** (sub-millisecond precision), streams per-client metrics into Grafana in real time, and computes fairness/aggregate statistics automatically.

If you've ever needed to ask *"are these N clients really being treated the same?"* — and wished the answer didn't require either a $50K test chamber or a research-grade tool with a 2012 UI — syncbench is built for that gap.

> **Status:** Phase 2 complete. Integrated web UI — select devices, start run, and watch live throughput curves all from one page. Wi-Fi 6 (HE80 OFDMA) ATF case study included as the first reference scenario; the orchestrator itself is transport-agnostic.

---

## Why this exists

Most multi-client benchmarks fall into one of three buckets:

- **`iperf3 -P N` from a single host** — easy, but everything runs from one NIC, so you're testing your own kernel scheduler, not the device under test
- **Research tools like [Flent](https://flent.org/)** — accurate and battle-tested, but built for batch runs and post-hoc matplotlib plots; not designed for live dashboards or modern CI pipelines
- **Commercial test chambers (Spirent octoBox, Candela LANforge)** — the gold standard, but six-figure entry price and built around virtual STAs rather than real heterogeneous clients

syncbench targets a different point in the design space:

| | iperf3 alone | Flent | Commercial | **syncbench** |
|---|:---:|:---:|:---:|:---:|
| Real heterogeneous clients | ❌ | partial | virtual STAs | ✅ |
| Sub-millisecond start sync | ❌ | best-effort | ✅ | ✅ |
| Real-time live chart | ❌ | ❌ | proprietary | ✅ |
| MQTT-orchestrated, CI-friendly | ❌ | ❌ | proprietary | ✅ |
| Open source | ✅ | ✅ (GPL) | ❌ | ✅ (Apache 2.0) |
| Cost | $0 | $0 | $$$$$ | $0 |

The orchestration layer doesn't care what you're benchmarking. Wi-Fi airtime fairness was the first scenario because it's a brutal stress test of synchronization (any timing skew distorts the fairness metric), but the same primitives apply to any "N endpoints, one event, measure who got what" problem.

---

## What's in the box

- **MQTT-orchestrated control plane** — a Mac/Linux controller broadcasts run parameters; agents on each client device subscribe and execute in lock-step
- **Sub-millisecond synchronized start** — NTP-anchored `sleep_until` (coarse sleep + busy-wait); measured **0–1 ms** offset across mixed ARM64 / x86_64 hardware
- **Pluggable platform adapters** — Raspberry Pi OS, Ubuntu/Debian today; macOS / Windows / Android on the Phase 2 roadmap. Same `PlatformAdapter` ABC, no scenario rewrites
- **Integrated web UI** — built-in Inspector at `localhost:8080`; select devices, start a run, and watch live per-second throughput curves — no separate Grafana tab needed
- **Automated reports** — Jain's Fairness Index, per-endpoint percentiles, and a markdown summary generated on every run
- **Wi-Fi band detection** — each agent reports its connected band (2.4G / 5G / 6G) derived from the platform adapter; visible in the Inspector device list
- **Standards-only data path** — uses `iw`, `nl80211`, `hostapd_cli`, Linux `debugfs`, and the `iperf3 --json` interface. No vendor-private APIs anywhere

---

## Where this fits

Use cases the framework is designed around:

- **Fairness validation** for any shared-resource system (Wi-Fi airtime, Ethernet LACP, mesh backhaul, shared storage I/O)
- **Multi-region / multi-client load tests** where coordinated start matters (CDN edge sync, distributed cache warm-up, multi-region S3 throughput)
- **CI-style nightly regression** of network performance — drop scenarios in `scenarios/`, schedule the runner, send Grafana/markdown to your dashboard
- **Engineering demos and bug reports** where "here's a live multi-client chart" beats a wall of CLI output
- **Heterogeneous client mix testing** — mixing real RPis, laptops, and (eventually) phones is something virtual-STA testbeds can't do

Use cases this is **not** trying to be:

- A replacement for Flent's test catalog (RRUL, rtt_fair, bufferbloat suite — Flent does these excellently and you should use it)
- A TR-398 certification rig — for vendor certification, use Spirent / Candela
- A microsecond-precision packet generator — sync precision is sub-millisecond at the application layer, not at the PHY

---

## Demo

> **v2 — Integrated Web UI** (Inspector with live Chart.js): select devices, start run, and watch throughput curves — all in one page.

### v2 · 5-STA: 5 × Raspberry Pi — Jain's FI = 0.886 ✅ Good

https://github.com/user-attachments/assets/e15f8b05-64dd-49f5-9f88-1296e3cfac76

### v2 · 6-STA: 5 × Raspberry Pi + 1 × Linux NB — Jain's FI = 0.521 ⚠️ Poor

https://github.com/user-attachments/assets/be0581c6-a20e-480c-8e1c-85e31639a57d

> The NB (Wi-Fi 6) dominates airtime — one device pulls JFI from **Good → Poor**. This is exactly the heterogeneous fairness gap syncbench is built to surface.

---

> **v1 — CLI + Grafana** (original Phase 1 demos)

### v1 · 2-STA: 2 × Raspberry Pi (Jain's FI = 0.999)

https://github.com/marsyanggo/syncbench/assets/50380018/5e907b10-2de8-434c-bcf9-475a82c2dacc

### v1 · 3-STA: 2 × Raspberry Pi + 1 × Linux NB (Jain's FI = 0.642)

https://github.com/marsyanggo/syncbench/assets/50380018/c9923234-d7bc-45a8-9ecb-760eac045d38

### v1 · 6-STA: 5 × Raspberry Pi + 1 × Linux NB (Jain's FI = 0.521)

https://github.com/user-attachments/assets/2159f23a-fef3-485e-b90e-8fb26ed8f379

> All runs: sync offset 0 ms, auto-generated report, real-time throughput curves.

### Phase 1 reference results (Wi-Fi ATF case study)

| Scenario | Jain's FI | Sync offset |
|---|---|---|
| 2-STA homogeneous (RPi × 2) | **0.999** | 0 ms |
| 3-STA heterogeneous (RPi × 2 + NB Wi-Fi 6) | **0.642** | 0–1 ms |
| 6-STA heterogeneous (RPi × 5 + NB Wi-Fi 6) | **0.521** | 0 ms |

> Side-finding from the heterogeneous run: the AX4200 (MT7986A / mt76) does not enforce ATF in HE80 OFDMA mode — `airtime_weight` is bypassed by the OFDMA RU scheduler. See [methodology.md](docs/methodology.md) for the full write-up. This is exactly the kind of "the tool surfaced something the spec didn't predict" outcome the framework is designed to enable.

---

## Quick Start

```bash
# Bring up the stack (Mosquitto + InfluxDB)
docker compose up -d

# Start the Inspector — device selector, run control, and live chart in one page
uv run atf-inspector
open http://localhost:8080
```

Select your online devices, set a duration, and press **Start Run**. Live per-second throughput curves appear as the test runs, Jain's Fairness Index and a markdown report are generated on completion.

Or run a scenario directly from the CLI:

```bash
uv run atf-run scenarios/04_six_sta_mixed.yaml
```

> **Grafana** is optional — useful for historical run comparison and advanced queries:
> ```bash
> docker compose --profile monitoring up -d grafana
> open http://localhost:3000   # admin / atf-grafana-2026
> ```

A scenario is a YAML file describing the endpoints, the synchronized event, and the success criteria. Anything you can express as "N agents, run this command at T+5 seconds, collect these metrics" can become a scenario.

---

## Architecture (one paragraph)

A controller publishes to an MQTT broker; agents on each client device subscribe to broadcast topics and report back on per-agent topics. The controller computes a `start_unix_ms` timestamp 5 seconds in the future, broadcasts it once, and every agent independently sleeps to that exact instant before launching its workload. Per-second metrics stream via MQTT into the Inspector's live chart; results are also written to InfluxDB for historical analysis. Full design: [docs/architecture.md](docs/architecture.md).

---

## Documentation

- **User guide:** [English](docs/user-guide-en.md) · [中文](docs/user-guide-zh.md)
- **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Methodology + Wi-Fi ATF case study:** [docs/methodology.md](docs/methodology.md)
- **Cross-platform roadmap:** [docs/multi-platform.md](docs/multi-platform.md) · [中文](docs/multi-platform-zh.md)
- **Dev setup:** [docs/development-setup.md](docs/development-setup.md)

---

## Roadmap

**Phase 1 — done.** Linux agents (RPi + x86_64), MQTT orchestration, sub-ms sync, InfluxDB, auto reports with Jain's FI, Wi-Fi ATF case study up to 6 STA.

**Phase 2 — done.** Integrated web UI: device selector, one-click run, native Chart.js live throughput, Jain's FI in-browser. Grafana demoted to optional. Per-device Wi-Fi band detection (2.4G / 5G / 6G).

**Phase 3 — planned.** macOS, Windows, and Android (via Termux) platform adapters. Goal: same scenario YAML runs unmodified across all four platforms.

**Phase 4 — planned.** Scale to 10–50 endpoints. Broker tuning, scenario sharding, optional RF-isolation testbed integration.

**Phase 5 — planned.** Non-Wi-Fi reference scenarios (multi-region cloud, mesh backhaul, distributed cache), MQTT auth + TLS, public-release hardening.

Contributions welcome — especially scenario contributions for non-Wi-Fi domains. If you've got a "fairness across N clients" question in your stack, opening an issue with the use case is the most useful thing you can do right now.

---

## Acknowledgements

This project stands on the shoulders of the bufferbloat / make-wifi-fast community, particularly the work of Toke Høiland-Jørgensen on mac80211 airtime fairness and the [Flent](https://flent.org/) network tester. syncbench's data path reads the same `debugfs` interfaces that Flent's `wifistats_iterate` does — the difference is in the orchestration model and the visualization layer, not in the underlying measurement.

---

## License

[Apache 2.0](LICENSE) — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

## References

- IEEE 802.11-2020
- [Linux mac80211](https://wireless.wiki.kernel.org/en/developers/documentation/mac80211)
- [OpenWrt](https://openwrt.org) · [iperf3](https://iperf.fr) · [mt76](https://github.com/openwrt/mt76)
- [Flent: The FLExible Network Tester](https://flent.org/) — prior art and intellectual debt
