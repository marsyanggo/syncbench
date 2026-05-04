# syncbench — Test Methodology

## Overview

syncbench measures Wi-Fi Airtime Fairness (ATF) by running synchronized iperf3 sessions across multiple stations (STAs) and quantifying throughput distribution using Jain's Fairness Index.

All measurements use only public kernel interfaces: `iw`, `nl80211`, `iperf3`, and Linux `debugfs`. No vendor-private APIs are used.

## Synchronization Method

Accurate simultaneous iperf3 start across N STAs is critical. The framework uses:

1. **NTP time source** — All STAs synchronize clocks via chrony (Linux STAs) or timed (macOS). Offset < 10 ms is required.
2. **Broadcast start time** — The controller broadcasts a `start_unix_ms` timestamp (T + 5 seconds) to all agents via MQTT.
3. **`sleep_until` algorithm** — Each agent uses a two-phase sleep:
   - *Coarse sleep*: `time.sleep()` until T − 20 ms (saves CPU)
   - *Busy-wait*: spin on `time.perf_counter()` for the final 20 ms (sub-millisecond precision)
4. **Measured sync offset** — `sync_offset_ms = actual_start_ms − target_start_ms`, recorded per STA per run.

**Observed sync precision:** 0–1 ms across ARM64 (Raspberry Pi) and x86_64 (Linux laptop).

## Metrics

### Jain's Fairness Index (JFI)

```
JFI = (Σ xi)² / (n × Σ xi²)
```

Where `xi` is the average throughput of STA `i` over the test duration.

- **JFI = 1.0**: perfect fairness (all STAs get equal throughput)
- **JFI = 1/n**: worst case (one STA gets all the bandwidth)
- **JFI ≥ 0.95**: "Excellent" (Phase 1 target for homogeneous STAs)

### Per-STA throughput statistics

Collected at 1-second intervals via iperf3 text-mode streaming (`--forceflush --interval 1`):

| Metric | Definition |
|---|---|
| `mean_mbps` | Arithmetic mean over test duration |
| `stdev_mbps` | Standard deviation (throughput variability) |
| `p95_mbps` | 95th percentile (steady-state throughput) |
| `retransmits` | Total TCP retransmissions (congestion/loss indicator) |
| `sync_offset_ms` | Actual start time − target start time |

### AP-side airtime (via mt76 debugfs)

```
/sys/kernel/debug/ieee80211/phy1/netdev:phy1-ap0/stations/{MAC}/airtime
```

Fields: `RX: <us>`, `TX: <us>` (cumulative microseconds). The collector computes delta percentage:

```
tx_pct = (tx_us_now − tx_us_prev) / 1_000_000 / dt_sec × 100
```

This gives the fraction of radio time allocated to each STA from the AP's perspective.

## Test Scenarios

### Scenario YAML structure

```yaml
extends: _base/normal.yaml    # inherit defaults
name: "Two STA Equal"
duration_sec: 60

preflight:
  expected_agents: ["rpi-sta-01", "rpi-sta-02"]
  software:
    ntp_synced_required: true
    max_ntp_offset_ms: 100.0

stations:
  - node: rpi-sta-01
    traffic:
      type: iperf3_tcp         # or iperf3_udp
      server: "atf-broker.local"
      # port: auto-assigned by orchestrator
      parallel: 1
```

### Bundled scenarios

| File | STAs | Duration | Purpose |
|---|---|---|---|
| `00_smoke_test.yaml` | 1 | 30s | Pipeline validation |
| `01_two_sta_equal.yaml` | 2 (RPi×2) | 60s | Homogeneous fairness baseline |
| `02_three_sta_mixed.yaml` | 3 (RPi×2 + NB) | 60s | Heterogeneous platform test |

## Traffic Direction

Each station can run iperf3 in one of three directions:

| Direction | Role | Notes |
|---|---|---|
| `uplink` | Device → Mac (device = client) | Default. One iperf3 server spawned on Mac per port. |
| `downlink` | Mac → Device (device = server) | Orchestrator spawns iperf3 client on Mac after agents bind ports (+1.5 s grace). |
| `bidirectional` | Both simultaneously (`--bidir`) | Reports TX + RX combined throughput. |

## QoS / DSCP Testing

### DSCP → 802.11 Access Category Mapping

The framework supports four 802.11 Access Categories via DSCP marking (`iperf3 --tos`):

| AC Label | DSCP Class | DSCP Value | `--tos` byte | 802.11 UP | 802.11 AC |
|---|---|---|---|---|---|
| `vo` | EF | 46 | `0xb8` | 6 | AC_VO |
| `vi` | AF31 | 26 | `0x68` | 4–5 | AC_VI |
| `be` | CS0 | 0 | `0x00` | 0 | AC_BE |
| `bk` | CS1 | 8 | `0x20` | 1–2 | AC_BK |

The DSCP value is set on the sender's IP socket via `setsockopt(IP_TOS)`. The AP and Linux mac80211 stack map it to the 802.11 QoS Control UP field, which determines which EDCA queue is used.

**Mapping chain:** `iperf3 --tos` → IP TOS field → `cfg80211_classify8021d()` (kernel) → 802.11 UP → EDCA AC queue.

### Observed QoS Behavior (AX4200, VHT80, 2 STAs)

Measured on 2026-05-04 with Mac mini (Ethernet) as controller and two RPi 500 STAs (VHT80, -38 dBm, 433 Mbps link rate).

**Single-stream baseline:**

| Direction | AC | Throughput |
|---|---|---|
| Downlink | BE | ~266 Mbps |
| Downlink | VI | ~195 Mbps |
| Downlink | VO | ~80 Mbps |

**Concurrent 2-STA results:**

| rpi-sta-01 | rpi-sta-02 | sta-01 result | sta-02 result | Total |
|---|---|---|---|---|
| Uplink BE | Uplink VI | 211 Mbps | 49 Mbps | 260 Mbps |
| Downlink BE | Downlink VI | 40 Mbps | 194 Mbps | 234 Mbps |
| Downlink BE | Downlink BE | 142 Mbps | 140 Mbps | 282 Mbps |
| Downlink BE | Downlink VO | 13 Mbps | 4.7 Mbps | 18 Mbps ← collapse |

### Key Finding 1: AC_VO Queue Overflow for Bulk TCP

**VO (DSCP EF) should not be used for high-throughput TCP testing.**

The AP's AC_VO queue is designed for voice calls (~64 Kbps–1 Mbps, small packets). Pushing bulk TCP (1500-byte MTU) through AC_VO causes:
- Queue overflow → packet drops → TCP retransmits → congestion collapse
- 2× VO concurrent: 45 + 2.7 Mbps with 115 + 30 TCP retransmits (vs 0 retransmits for 2× BE)
- 1 BE + 1 VO: VO queue overflow steals airtime from BE → both streams collapse to ~13 + 4.7 Mbps

**Recommendation:** Use VI (`0x68`) for high-throughput QoS comparison. VI queue is sized for video (higher bitrate) and correctly demonstrates AP downlink prioritization (194 Mbps VI vs 40 Mbps BE).

### Key Finding 2: WMM EDCA Asymmetry — VI Behaves Opposite on Uplink vs Downlink

When running 1× uplink BE + 1× uplink VI concurrently:
- BE: **211 Mbps**, VI: **49 Mbps** — VI is *slower* than BE on uplink

When running 1× downlink BE + 1× downlink VI concurrently:
- BE: **40 Mbps**, VI: **194 Mbps** — VI is *faster* than BE on downlink

**Root cause:** WMM defines two independent EDCA parameter sets:

1. **STA EDCA** (advertised in AP beacon, governs uplink):
   - AC_VI: AIFSN=2, CWmin=3, CWmax=7, **TXOP limit = 3.008 ms**
   - AC_BE: AIFSN=3, CWmin=4, CWmax=10, **TXOP limit = 0 (unlimited)**
   - On uplink, VI wins contention more often but gets a short TXOP each time → limited bytes per transmission → low throughput for bulk data

2. **AP EDCA** (internal to AP, governs downlink):
   - The AP uses its own EDCA parameters for its own transmissions
   - AC_VI downlink TXOP is typically larger, allowing the AP to send more VI frames per slot
   - Downlink VI frames are also placed in the higher-priority AC_VI queue at the AP

**Practical implication:** QoS differentiation effectiveness depends strongly on direction:
- Downlink: VI clearly shows higher priority than BE (expected behavior for multimedia streaming)
- Uplink: VI appears slower than BE for bulk TCP due to TXOP limit mismatch

### Key Finding 3: DSCP Marking is Confirmed Working

The DSCP → TID mapping is verified as active on the AP:
- `wmm_enabled=1` in hostapd config
- Distinct throughput differences between AC classes in both directions confirm the AP is reading DSCP and routing to separate queues
- Single-stream VO at 80 Mbps (vs BE at 266 Mbps) shows the AP applies VO queue constraints even for single streams

## Known Limitations

### AX4200 (MT7986A / mt76) — ATF not effective in HE80 mode

The AX4200 advertises ATF capability (`AIRTIME_FAIRNESS`, `AQL`) but all tested `airtime_mode` values (0, 1, 2) and per-STA `airtime_weight` settings produce statistically identical throughput distributions in HE80 (Wi-Fi 6) mode.

Root cause: Wi-Fi 6 HE80 uses OFDMA, where the AP allocates resource units (RUs) to multiple STAs simultaneously. The mac80211 airtime fairness scheduler — which `airtime_weight` targets — operates on the legacy single-user TXQ path and is bypassed in OFDMA mode. The mt76 HE80 OFDMA RU scheduler does not expose per-STA weight controls.

**Workaround for ATF on/off comparison:** Use `htmode=VHT80` (Wi-Fi 5, CSMA/CA) where the per-STA airtime scheduler is active.

### Heterogeneous Wi-Fi generations

When mixing Wi-Fi 5 (1×1 SISO) and Wi-Fi 6 (2×2 MIMO) devices, throughput fairness cannot reach JFI = 1.0 without artificial rate limiting. The PHY rate gap (approximately 4–5× for the tested hardware) is the dominant factor, exceeding what any airtime scheduler can compensate.

This is expected and physically motivated: ATF guarantees equal *airtime*, not equal *throughput*. Equal airtime with unequal PHY rates produces proportionally unequal throughput.

## Statistical Notes

- Each reported value is a single 60-second run mean. For publishable results, run N ≥ 5 trials and report confidence intervals.
- Back-to-back runs may show correlation due to Wi-Fi channel state memory. Allow 30 seconds between runs.
- Grafana auto-refresh at 5 seconds is sufficient for real-time monitoring but should not be used for precise timing analysis.
