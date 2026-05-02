from typing import Literal
from pydantic import BaseModel, Field


class TrafficConfig(BaseModel):
    type: Literal["iperf3_tcp", "iperf3_udp"] = "iperf3_tcp"
    server: str                        # iperf3 server IP (Mac mini)
    port: int = 5201
    bandwidth_mbps: int | None = None  # UDP only
    parallel: int = 1
    direction: Literal["uplink", "downlink", "bidirectional"] = "uplink"
    # uplink:        device → Mac (device=client, Mac=server)  [default, existing behaviour]
    # downlink:      Mac → device (Mac=client, device=server)
    # bidirectional: both simultaneously via iperf3 --bidir


class StationConfig(BaseModel):
    node: str                          # agent_id, e.g. "rpi-sta-01"
    traffic: TrafficConfig


class SoftwareRequirements(BaseModel):
    ntp_synced_required: bool = True
    max_ntp_offset_ms: float = 100.0
    max_mqtt_rtt_ms: float = 200.0
    min_cpu_idle_pct: float = 50.0


class PreflightConfig(BaseModel):
    expected_agents: list[str]
    software: SoftwareRequirements = Field(default_factory=SoftwareRequirements)


class Scenario(BaseModel):
    name: str
    description: str = ""
    duration_sec: int
    warmup_sec: int = 0
    stations: list[StationConfig]
    preflight: PreflightConfig
    extends: str | None = None         # relative path to base yaml (stripped before parse)
