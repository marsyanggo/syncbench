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
