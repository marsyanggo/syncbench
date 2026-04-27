# syncbench — Architecture

## Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Mac mini (Controller)                                       │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ atf-run  │  │inspector │  │atf-report│  │ap-collect │  │
│  │   CLI    │  │  :8080   │  │   CLI    │  │   :SSH    │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘  │
│       │              │              │               │        │
│  ┌────▼──────────────▼──────────────▼───────────────▼────┐  │
│  │             Orchestrator + MQTT Bus                    │  │
│  └────────────────────┬──────────────────────────────────┘  │
│                       │                                      │
│  ┌────────────────────▼──────────────────────────────────┐  │
│  │  Docker: Mosquitto (1883) · InfluxDB (8086)            │  │
│  │          Grafana (3000)                                │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ Ethernet
                    ┌──────▼──────┐
                    │   AX4200    │ ← device-under-test
                    │  (OpenWrt)  │
                    └──────┬──────┘
                           │ 5GHz Wi-Fi
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │rpi-sta-01│    │rpi-sta-02│    │linux-nb-01│
    │ atf-agent│    │ atf-agent│    │ atf-agent │
    └──────────┘    └──────────┘    └──────────┘
```

## Component Responsibilities

### Controller (Mac mini)

| Component | File | Role |
|---|---|---|
| `atf-run` CLI | `controller/atf_ctrl/cli.py` | Entry point, loads scenario, runs orchestrator |
| Orchestrator | `controller/atf_ctrl/orchestrator.py` | Coordinates agents via MQTT, manages iperf3 servers, writes metrics |
| Inspector | `controller/atf_ctrl/inspector/server.py` | FastAPI + SSE real-time agent status dashboard |
| InfluxDB writer | `controller/atf_ctrl/metrics/influx_writer.py` | Writes run_summary to InfluxDB |
| AP Collector | `controller/atf_ctrl/collector/ap_collector.py` | SSH into AP, reads mt76 debugfs airtime stats |
| Reporter | `controller/atf_ctrl/reporter/reporter.py` | Generates markdown report with Jain's FI from InfluxDB |
| Scenario loader | `controller/atf_ctrl/scenarios/loader.py` | Parses YAML scenarios with deep-merge inheritance |

### Agent (Raspberry Pi / Linux device)

| Component | File | Role |
|---|---|---|
| State machine | `agent/atf_agent/main.py` | BOOT→IDLE→PREPARING→ARMED→RUNNING→REPORTING→IDLE |
| iperf3 runner | `agent/atf_agent/traffic/iperf3.py` | Text-mode streaming, `--forceflush`, per-second callbacks |
| Sync | `shared/sync.py` | NTP-aware `sleep_until` (coarse sleep + busy-wait) |
| Platform adapter | `agent/atf_agent/platform/` | OS abstraction (Linux/macOS) for NTP, Wi-Fi, MAC |
| MQTT bus | `shared/mqtt_bus.py` | Shared pub/sub with envelope injection |

## Message Flow

```
atf-run                     MQTT broker          Agent (N instances)
  │                              │                      │
  │── prepare {station_traffic} ──────────────────────→ │
  │                              │        ← ack ──────── │
  │── start_at {unix_ms} ────────────────────────────→  │
  │                              │   (sleep_until T)     │
  │                              │   iperf3 running...   │
  │                              │← live/{run_id} ─────── │ (1 Hz)
  │   [InfluxDB write] ──────────┘                       │
  │                              │← result/{run_id} ───── │
  │── stop ──────────────────────────────────────────→   │
  │── teardown ──────────────────────────────────────→   │
  │   [InfluxDB write: run_summary]                       │
  │   [atf-report]                                        │
```

## MQTT Topics

| Topic | Direction | QoS | Content |
|---|---|---|---|
| `atf/ctrl/broadcast/prepare` | ctrl→agents | 1 | run_id, station_traffic map |
| `atf/ctrl/broadcast/start_at` | ctrl→agents | 1 | run_id, start_unix_ms |
| `atf/ctrl/broadcast/stop` | ctrl→agents | 1 | run_id |
| `atf/ctrl/broadcast/teardown` | ctrl→agents | 1 | run_id |
| `atf/agent/{id}/ack/{msg_id}` | agent→ctrl | 1 | ok flag |
| `atf/agent/{id}/heartbeat` | agent→ctrl | 0 | state, ntp_offset_ms |
| `atf/agent/{id}/status` | agent→ctrl | 1 retained | state, wifi_mac |
| `atf/agent/{id}/live/{run_id}` | agent→ctrl | 0 | ts_ms, throughput_mbps |
| `atf/agent/{id}/result/{run_id}` | agent→ctrl | 1 | summary, samples |

## InfluxDB Measurements

| Measurement | Tags | Fields | Written by |
|---|---|---|---|
| `throughput` | run_id, agent_id, scenario | throughput_mbps, retransmits | orchestrator (live) |
| `run_summary` | run_id, agent_id, scenario | mean_mbps, stdev_mbps, p95_mbps, retransmits, sync_offset_ms | orchestrator (end) |
| `ap_airtime` | agent_id, mac | tx_pct, rx_pct | ap_collector |
