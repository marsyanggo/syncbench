"""iperf3 runner — wraps iperf3 CLI, parses JSON output, returns structured result.

Usage:
    result = run(server="192.168.1.100", duration=30)
    for s in result.samples:
        print(f"{s.throughput_mbps:.1f} Mbps  retransmits={s.retransmits}")
"""

import json
import math
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class ThroughputSample:
    ts_ms: int            # epoch ms at interval start
    interval_start: float # seconds from test start
    interval_end: float
    throughput_mbps: float
    retransmits: int
    lost_pct: float | None = None  # UDP only


@dataclass
class Iperf3Result:
    server: str
    protocol: str         # "tcp" | "udp"
    duration_sec: float
    samples: list[ThroughputSample] = field(default_factory=list)
    # Summary (filled after all intervals)
    throughput_mean_mbps: float = 0.0
    throughput_stdev_mbps: float = 0.0
    throughput_p95_mbps: float = 0.0
    total_retransmits: int = 0
    lost_pct: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.samples) > 0


def run(
    server: str,
    port: int = 5201,
    duration: int = 30,
    protocol: str = "tcp",
    bandwidth_mbps: int | None = None,  # UDP only
    parallel: int = 1,
) -> Iperf3Result:
    """Run iperf3 client and return parsed result.

    Blocks until the test completes (duration + ~2s overhead).
    """
    cmd = [
        "iperf3",
        "--client", server,
        "--port", str(port),
        "--time", str(duration),
        "--interval", "1",
        "--json",
        "--parallel", str(parallel),
    ]
    if protocol == "udp":
        cmd.append("--udp")
        if bandwidth_mbps:
            cmd += ["--bandwidth", f"{bandwidth_mbps}M"]

    result = Iperf3Result(server=server, protocol=protocol, duration_sec=duration)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 30,
        )
    except subprocess.TimeoutExpired:
        result.error = "iperf3 process timed out"
        return result
    except FileNotFoundError:
        result.error = "iperf3 not found — install with: apt install iperf3"
        return result

    if proc.returncode != 0:
        # iperf3 sometimes puts error in JSON, sometimes in stderr
        try:
            data = json.loads(proc.stdout)
            result.error = data.get("error", proc.stderr.strip())
        except json.JSONDecodeError:
            result.error = proc.stderr.strip() or f"exit code {proc.returncode}"
        return result

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result.error = "failed to parse iperf3 JSON output"
        return result

    if "error" in data:
        result.error = data["error"]
        return result

    # Parse per-interval samples
    test_start_ms = int(data["start"]["timestamp"]["timemillisecs"])
    is_udp = data["start"]["test_start"]["protocol"] == "UDP"

    for interval in data.get("intervals", []):
        s = interval["sum"]
        # skip omitted warmup intervals
        if s.get("omitted"):
            continue
        interval_start = s["start"]
        ts_ms = test_start_ms + int(interval_start * 1000)
        mbps = s["bits_per_second"] / 1_000_000

        lost_pct = None
        if is_udp:
            packets = s.get("packets", 0)
            lost = s.get("lost_packets", 0)
            lost_pct = (lost / packets * 100) if packets > 0 else 0.0

        result.samples.append(ThroughputSample(
            ts_ms=ts_ms,
            interval_start=interval_start,
            interval_end=s["end"],
            throughput_mbps=mbps,
            retransmits=s.get("retransmits", 0),
            lost_pct=lost_pct,
        ))

    if not result.samples:
        result.error = "no interval data in iperf3 output"
        return result

    # Compute summary statistics
    values = [s.throughput_mbps for s in result.samples]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    sorted_values = sorted(values)
    p95_idx = int(len(sorted_values) * 0.95)

    result.throughput_mean_mbps = mean
    result.throughput_stdev_mbps = math.sqrt(variance)
    result.throughput_p95_mbps = sorted_values[min(p95_idx, len(sorted_values) - 1)]
    result.total_retransmits = sum(s.retransmits for s in result.samples)

    if is_udp:
        lost_values = [s.lost_pct for s in result.samples if s.lost_pct is not None]
        result.lost_pct = sum(lost_values) / len(lost_values) if lost_values else 0.0

    return result
