"""iperf3 runner — streams per-second samples in real-time.

Runs iperf3 in text mode (no --json) and parses interval lines as they arrive.
Each parsed sample is passed to on_sample() callback immediately, enabling
real-time MQTT publishing without waiting for the full test to finish.

Usage:
    result = run(server="192.168.1.100", duration=30,
                 on_sample=lambda s: print(s.throughput_mbps))
"""

import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable


def _find_iperf3() -> str:
    return shutil.which("iperf3") or shutil.which("iperf3", path="/usr/bin:/usr/local/bin:/usr/sbin") or "iperf3"

# TCP interval line — client side (has retransmits + cwnd):
# [  5]   0.00-1.00   sec  16.1 MBytes   135 Mbits/sec    0    321 KBytes
# [  5][TX-C]   0.00-1.00   sec  6.38 MBytes  53.4 Mbits/sec    0    324 KBytes  (bidir)
_TCP_RE = re.compile(
    r"\[\s*\d+\](?:\[TX-C\])?\s+(\d+\.\d+)-(\d+\.\d+)\s+sec\s+"
    r"[\d.]+\s+\w+Bytes\s+([\d.]+)\s+Mbits/sec\s+(\d+)"
)

# TCP interval line — server side or bidir RX (no retransmits column):
# [  5]   0.00-1.00   sec  16.1 MBytes   135 Mbits/sec
# [  7][RX-C]   0.00-1.00   sec  23.9 MBytes   200 Mbits/sec   (bidir downlink)
_TCP_SERVER_RE = re.compile(
    r"\[\s*\d+\](?:\[RX-C\])?\s+(\d+\.\d+)-(\d+\.\d+)\s+sec\s+"
    r"[\d.]+\s+\w+Bytes\s+([\d.]+)\s+Mbits/sec\s*$"
)

# UDP interval line:
# [  5]   0.00-1.00   sec  1.25 MBytes  10.5 Mbits/sec  0.023 ms  0/892 (0%)
_UDP_RE = re.compile(
    r"\[\s*\d+\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec\s+"
    r"[\d.]+\s+\w+Bytes\s+([\d.]+)\s+Mbits/sec\s+"
    r"[\d.]+\s+ms\s+\d+/\d+\s+\(([\d.]+)%\)"
)


@dataclass
class ThroughputSample:
    ts_ms: int
    interval_start: float
    interval_end: float
    throughput_mbps: float
    retransmits: int
    lost_pct: float | None = None


@dataclass
class Iperf3Result:
    server: str
    protocol: str
    duration_sec: float
    samples: list[ThroughputSample] = field(default_factory=list)
    throughput_mean_mbps: float = 0.0
    throughput_stdev_mbps: float = 0.0
    throughput_p95_mbps: float = 0.0
    total_retransmits: int = 0
    lost_pct: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and len(self.samples) > 0


_AC_TOS = {"vo": "0xb8", "vi": "0x68", "be": "0x00", "bk": "0x20"}


def run(
    server: str,
    port: int = 5201,
    duration: int = 30,
    protocol: str = "tcp",
    bandwidth_mbps: int | None = None,
    parallel: int = 1,
    direction: str = "uplink",
    ac: str = "be",
    on_sample: Callable[[ThroughputSample], None] | None = None,
) -> Iperf3Result:
    """Run iperf3 client (uplink/bidirectional), streaming per-second samples.

    direction:
      uplink        — device→Mac (client mode, existing behaviour)
      bidirectional — device↔Mac simultaneously (--bidir flag)
      downlink      — Mac→device; caller must use run_server() on the device
                      and spawn the iperf3 client on the Mac side instead.

    Blocks until the test completes. on_sample() is called from this thread
    for each 1-second interval as it arrives.
    """
    cmd = [
        _find_iperf3(), "--client", server,
        "--port", str(port),
        "--time", str(duration),
        "--interval", "1",
        "--parallel", str(parallel),
        "--forceflush",
    ]
    if direction == "bidirectional":
        cmd.append("--bidir")
    tos = _AC_TOS.get(ac, "0x00")
    if tos != "0x00":
        cmd += ["--tos", tos]
    if protocol == "udp":
        cmd.append("--udp")
        if bandwidth_mbps:
            cmd += ["--bandwidth", f"{bandwidth_mbps}M"]

    result = Iperf3Result(server=server, protocol=protocol, duration_sec=duration)
    test_start_ms = int(time.time() * 1000)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered: return each line as soon as it arrives
        )
    except FileNotFoundError:
        result.error = "iperf3 not found — install with: apt install iperf3"
        return result

    is_udp = protocol == "udp"
    is_bidir = direction == "bidirectional"
    samples: list[ThroughputSample] = []
    # bidir: buffer TX-C sample until matching RX-C arrives, then combine
    _bidir_tx: ThroughputSample | None = None

    for line in proc.stdout:
        if is_bidir:
            # TX-C line → buffer it
            m_tx = _TCP_RE.search(line)
            if m_tx and '[TX-C]' in line:
                t0, t1 = float(m_tx.group(1)), float(m_tx.group(2))
                if t1 - t0 <= 1.5:
                    _bidir_tx = ThroughputSample(
                        ts_ms=test_start_ms + int(t0 * 1000),
                        interval_start=t0, interval_end=t1,
                        throughput_mbps=float(m_tx.group(3)),
                        retransmits=int(m_tx.group(4)), lost_pct=None,
                    )
                continue
            # RX-C line → combine with buffered TX-C
            m_rx = _TCP_SERVER_RE.search(line.rstrip())
            if m_rx and '[RX-C]' in line:
                t0, t1 = float(m_rx.group(1)), float(m_rx.group(2))
                if t1 - t0 <= 1.5 and _bidir_tx is not None:
                    sample = ThroughputSample(
                        ts_ms=_bidir_tx.ts_ms,
                        interval_start=_bidir_tx.interval_start,
                        interval_end=_bidir_tx.interval_end,
                        # combined TX+RX throughput
                        throughput_mbps=_bidir_tx.throughput_mbps + float(m_rx.group(3)),
                        retransmits=_bidir_tx.retransmits, lost_pct=None,
                    )
                    _bidir_tx = None
                    samples.append(sample)
                    if on_sample:
                        on_sample(sample)
            continue
        # uplink or downlink (non-bidir)
        sample = _parse_line(line, is_udp, test_start_ms)
        if sample is None:
            continue
        samples.append(sample)
        if on_sample:
            on_sample(sample)

    proc.wait(timeout=duration + 30)

    if proc.returncode != 0:
        stderr = proc.stderr.read()
        result.error = stderr.strip() or f"exit code {proc.returncode}"
        return result

    if not samples:
        result.error = "no interval data received from iperf3"
        return result

    result.samples = samples
    _compute_stats(result)
    return result


def run_server(
    port: int = 5201,
    duration: int = 30,
    on_sample: Callable[[ThroughputSample], None] | None = None,
) -> Iperf3Result:
    """Run iperf3 in server mode (one-shot) for downlink direction.

    Spawns `iperf3 --server --one-off` on the given port.  The Mac-side
    orchestrator will connect as client.  Exits after one client connection.
    Streams per-second samples via on_sample as they arrive.
    """
    cmd = [
        _find_iperf3(), "--server", "--one-off",
        "--port", str(port),
        "--interval", "1",
        "--forceflush",
    ]
    result = Iperf3Result(server="(server)", protocol="tcp", duration_sec=duration)
    test_start_ms = int(time.time() * 1000)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        result.error = "iperf3 not found"
        return result

    samples: list[ThroughputSample] = []
    for line in proc.stdout:
        m = _TCP_SERVER_RE.search(line.rstrip())
        if not m:
            continue
        t0, t1 = float(m.group(1)), float(m.group(2))
        if t1 - t0 > 1.5:  # skip final summary line
            continue
        sample = ThroughputSample(
            ts_ms=test_start_ms + int(t0 * 1000),
            interval_start=t0,
            interval_end=t1,
            throughput_mbps=float(m.group(3)),
            retransmits=0,  # server side has no retransmits column
            lost_pct=None,
        )
        samples.append(sample)
        if on_sample:
            on_sample(sample)

    proc.wait(timeout=duration + 30)

    if not samples:
        result.error = "no interval data from iperf3 server"
        return result

    result.samples = samples
    _compute_stats(result)
    return result


def _parse_line(
    line: str, is_udp: bool, test_start_ms: int
) -> ThroughputSample | None:
    if is_udp:
        m = _UDP_RE.search(line)
        if not m:
            return None
        t0, t1 = float(m.group(1)), float(m.group(2))
        if t1 - t0 > 1.5:  # skip final summary line
            return None
        return ThroughputSample(
            ts_ms=test_start_ms + int(t0 * 1000),
            interval_start=t0,
            interval_end=t1,
            throughput_mbps=float(m.group(3)),
            retransmits=0,
            lost_pct=float(m.group(4)),
        )
    else:
        m = _TCP_RE.search(line)
        if not m:
            return None
        t0, t1 = float(m.group(1)), float(m.group(2))
        if t1 - t0 > 1.5:  # skip final summary line
            return None
        return ThroughputSample(
            ts_ms=test_start_ms + int(t0 * 1000),
            interval_start=t0,
            interval_end=t1,
            throughput_mbps=float(m.group(3)),
            retransmits=int(m.group(4)),
            lost_pct=None,
        )


def _compute_stats(result: Iperf3Result) -> None:
    values = [s.throughput_mbps for s in result.samples]
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    sorted_v = sorted(values)
    p95_idx = max(0, int(n * 0.95) - 1)

    result.throughput_mean_mbps = mean
    result.throughput_stdev_mbps = math.sqrt(variance)
    result.throughput_p95_mbps = sorted_v[p95_idx]
    result.total_retransmits = sum(s.retransmits for s in result.samples)
    if result.samples[0].lost_pct is not None:
        result.lost_pct = sum(s.lost_pct for s in result.samples if s.lost_pct is not None) / n
