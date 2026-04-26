"""iperf3 runner tests.

Requires iperf3 installed and available on localhost:5201.
Run with: uv run pytest agent/tests/test_iperf3.py -v
"""

import subprocess
import time

import pytest

from agent.atf_agent.traffic.iperf3 import run


@pytest.fixture(scope="module", autouse=True)
def iperf3_server():
    """Start a local iperf3 server for the duration of the test module."""
    proc = subprocess.Popen(
        ["iperf3", "--server", "--daemon", "--logfile", "/tmp/iperf3-test.log"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield
    proc.terminate()
    subprocess.run(["pkill", "-f", "iperf3 --server"], capture_output=True)


def test_tcp_basic():
    result = run(server="localhost", duration=3)

    assert result.ok, f"iperf3 failed: {result.error}"
    assert len(result.samples) == 3
    assert result.throughput_mean_mbps > 0
    assert result.throughput_stdev_mbps >= 0
    assert result.throughput_p95_mbps > 0
    assert result.total_retransmits >= 0
    assert result.lost_pct is None  # TCP has no lost_pct


def test_samples_have_timestamps():
    result = run(server="localhost", duration=2)

    assert result.ok
    for s in result.samples:
        assert s.ts_ms > 0
        assert s.throughput_mbps > 0
        assert s.interval_end > s.interval_start


def test_samples_are_sequential():
    result = run(server="localhost", duration=3)

    assert result.ok
    for i in range(1, len(result.samples)):
        assert result.samples[i].interval_start >= result.samples[i - 1].interval_end - 0.01


def test_server_unreachable():
    result = run(server="127.0.0.2", port=59999, duration=3)

    assert not result.ok
    assert result.error is not None


def test_udp_basic():
    result = run(server="localhost", duration=3, protocol="udp", bandwidth_mbps=10)

    assert result.ok, f"iperf3 UDP failed: {result.error}"
    assert result.lost_pct is not None
    assert result.lost_pct >= 0
