"""Smoke test: MQTTBus publish → subscribe roundtrip.

Requires the docker-compose Mosquitto broker to be running on localhost:1883.
Run with: uv run pytest controller/tests/test_mqtt_bus.py -v
"""

import threading
import time

import pytest

from controller.atf_ctrl.mqtt_bus import MQTTBus

BROKER = "localhost"
PORT = 1883
TOPIC = "atf/test/mqtt_bus_smoke"
TIMEOUT = 5.0


def test_pubsub_roundtrip():
    received: list[dict] = []
    ready = threading.Event()
    done = threading.Event()

    sub = MQTTBus()
    sub.connect(BROKER, PORT, client_id="test-sub")
    sub.subscribe(TOPIC, lambda topic, payload: (received.append(payload), done.set()))
    sub.loop_start()
    time.sleep(0.3)  # wait for sub to connect

    pub = MQTTBus()
    pub.connect(BROKER, PORT, client_id="test-pub")
    pub.loop_start()
    pub.publish(TOPIC, {"hello": "atf", "value": 42})

    assert done.wait(timeout=TIMEOUT), "No message received within timeout"

    pub.loop_stop()
    sub.loop_stop()

    assert len(received) == 1
    msg = received[0]
    assert msg["hello"] == "atf"
    assert msg["value"] == 42
    # envelope fields
    assert msg["v"] == 1
    assert isinstance(msg["ts"], int)
    assert isinstance(msg["msg_id"], str) and len(msg["msg_id"]) == 26  # ULID length


def test_lwt_payload_format():
    """Verify LWT topic + payload can be set without error."""
    bus = MQTTBus()
    bus.connect(
        BROKER, PORT,
        client_id="test-lwt",
        lwt_topic="atf/agent/test-lwt/status",
        lwt_payload={"state": "OFFLINE"},
    )
    bus.loop_start()
    time.sleep(0.2)
    bus.disconnect()
    bus.loop_stop()


def test_wildcard_subscription():
    received: list[tuple] = []
    done = threading.Event()

    sub = MQTTBus()
    sub.connect(BROKER, PORT, client_id="test-wildcard-sub")
    sub.subscribe(
        "atf/test/wildcard/+",
        lambda topic, payload: (received.append((topic, payload)), done.set()),
    )
    sub.loop_start()
    time.sleep(0.3)

    pub = MQTTBus()
    pub.connect(BROKER, PORT, client_id="test-wildcard-pub")
    pub.loop_start()
    pub.publish("atf/test/wildcard/sta-01", {"agent_id": "sta-01"})

    assert done.wait(timeout=TIMEOUT)

    pub.loop_stop()
    sub.loop_stop()

    topic, payload = received[0]
    assert topic == "atf/test/wildcard/sta-01"
    assert payload["agent_id"] == "sta-01"
