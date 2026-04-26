import pytest
from controller.atf_ctrl.scenarios.loader import load
from controller.atf_ctrl.scenarios.models import Scenario


def test_load_smoke_test():
    s = load("00_smoke_test.yaml")
    assert isinstance(s, Scenario)
    assert s.name == "Smoke Test — Single STA"
    assert s.duration_sec == 30
    assert len(s.stations) == 1
    assert s.stations[0].node == "rpi-sta-01"
    assert s.stations[0].traffic.type == "iperf3_tcp"
    assert s.stations[0].traffic.server == "192.168.1.100"


def test_extends_merges_preflight_defaults():
    s = load("00_smoke_test.yaml")
    # From _base/normal.yaml defaults
    assert s.preflight.software.max_mqtt_rtt_ms == 200.0
    assert s.preflight.software.min_cpu_idle_pct == 50.0
    # Overridden in 00_smoke_test.yaml
    assert s.preflight.software.max_ntp_offset_ms == 100.0
    assert s.preflight.expected_agents == ["rpi-sta-01"]


def test_load_two_sta():
    s = load("01_two_sta_equal.yaml")
    assert len(s.stations) == 2
    assert {st.node for st in s.stations} == {"rpi-sta-01", "rpi-sta-02"}


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load("nonexistent.yaml")


def test_invalid_yaml_raises():
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        suffix=".yaml", dir="scenarios", mode="w", delete=False
    ) as f:
        f.write("name: Test\nduration_sec: not_a_number\nstations: []\n")
        tmp = f.name
    try:
        with pytest.raises(ValueError, match="Invalid scenario"):
            load(tmp)
    finally:
        os.unlink(tmp)
