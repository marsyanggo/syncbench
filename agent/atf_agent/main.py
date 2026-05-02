"""ATF Agent — entry point and state machine.

States:  BOOT → IDLE → PREPARING → ARMED → RUNNING → REPORTING → IDLE
         Any state → ERROR (on fatal failure)
         Any state → OFFLINE (LWT, on unexpected disconnect)

Usage:
    uv run atf-agent --broker localhost --port 1883 --agent-id rpi-sta-01
"""

import argparse
import logging
import platform
import signal
import sys
import threading
import time

from agent.atf_agent.traffic.iperf3 import run as run_iperf3
from shared.mqtt_bus import MQTTBus
from shared.sync import sleep_until

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("atf.agent")

VERSION = "0.1.0"
HEARTBEAT_INTERVAL = 1.0


def _make_platform_adapter():
    os_name = platform.system()
    if os_name == "Linux":
        from agent.atf_agent.platform.linux import LinuxAdapter
        return LinuxAdapter()
    elif os_name == "Darwin":
        from agent.atf_agent.platform.macos import MacOSAdapter
        return MacOSAdapter()
    else:
        raise RuntimeError(f"Unsupported platform: {os_name}")


class ATFAgent:
    def __init__(self, broker: str, port: int, agent_id: str) -> None:
        self.agent_id = agent_id
        self._broker = broker
        self._port = port
        self._state = "BOOT"
        self._running = False
        self._platform = _make_platform_adapter()
        self._bus = MQTTBus()

        self._status_topic = f"atf/agent/{agent_id}/status"
        self._heartbeat_topic = f"atf/agent/{agent_id}/heartbeat"

        # Set during prepare, used during start_at
        self._current_run_id: str | None = None
        self._traffic_config: dict | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        platform_info = self._platform.get_platform_info()
        logger.info(
            "Starting agent %s on %s/%s (%s)",
            self.agent_id, platform_info.os, platform_info.arch, platform_info.model,
        )
        self._bus.connect(
            self._broker, self._port,
            client_id=self.agent_id,
            lwt_topic=self._status_topic,
            lwt_payload={"state": "OFFLINE", "agent_id": self.agent_id},
        )
        self._bus.loop_start()
        self._subscribe()
        self._set_state("BOOT")
        self._wait_for_ntp(timeout=30)
        self._set_state("IDLE")
        self._running = True
        self._run_heartbeat_loop()

    def stop(self) -> None:
        logger.info("Stopping agent %s", self.agent_id)
        self._running = False
        self._set_state("OFFLINE")
        self._bus.loop_stop()
        self._bus.disconnect()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        self._state = state
        logger.info("State → %s", state)
        self._bus.publish(
            self._status_topic,
            {
                "state": state,
                "agent_id": self.agent_id,
                "agent_version": VERSION,
                "platform": self._platform.get_platform_info().os,
                "wifi_mac": self._platform.get_wifi_mac(),
                "current_run_id": self._current_run_id,
            },
            qos=1,
            retain=True,
        )

    # ------------------------------------------------------------------
    # Heartbeat loop (main thread)
    # ------------------------------------------------------------------

    def _run_heartbeat_loop(self) -> None:
        logger.info("Heartbeat loop started (%.1fs interval)", HEARTBEAT_INTERVAL)
        while self._running:
            self._bus.publish(
                self._heartbeat_topic,
                {
                    "agent_id": self.agent_id,
                    "state": self._state,
                    "ntp_offset_ms": self._platform.get_ntp_offset_ms(),
                    "ntp_synced": self._platform.is_ntp_synced(),
                    "band": self._platform.get_band(),
                    "ip": self._platform.get_wifi_ip(),
                },
                qos=0,
            )
            time.sleep(HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------
    # Command handlers (called from MQTT thread)
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        self._bus.subscribe("atf/ctrl/broadcast/+", self._on_broadcast, qos=1)
        self._bus.subscribe(
            f"atf/ctrl/unicast/agent/{self.agent_id}/+", self._on_unicast, qos=1
        )

    def _on_broadcast(self, topic: str, payload: dict) -> None:
        cmd = topic.split("/")[-1]
        logger.info("Broadcast: %s", cmd)
        handlers = {
            "prepare": self._handle_prepare,
            "start_at": self._handle_start_at,
            "stop": self._handle_stop,
            "teardown": lambda p: self._set_state("IDLE"),
        }
        if cmd in handlers:
            handlers[cmd](payload)

    def _on_unicast(self, topic: str, payload: dict) -> None:
        logger.info("Unicast: %s", topic.split("/")[-1])

    def _handle_prepare(self, payload: dict) -> None:
        if self._state != "IDLE":
            logger.warning("prepare in state %s — ignoring", self._state)
            return

        # Skip prepare if this agent isn't in the scenario
        station_traffic = payload.get("station_traffic", {})
        if self.agent_id not in station_traffic:
            logger.info("Not in scenario (agents: %s), skipping prepare", list(station_traffic))
            return

        self._set_state("PREPARING")
        self._current_run_id = payload.get("run_id")
        self._traffic_config = station_traffic[self.agent_id]

        # Acknowledge
        self._bus.publish(
            f"atf/agent/{self.agent_id}/ack/{payload.get('msg_id', 'unknown')}",
            {"run_id": self._current_run_id, "agent_id": self.agent_id, "ok": True},
        )
        self._set_state("ARMED")

    def _handle_start_at(self, payload: dict) -> None:
        if self._state != "ARMED":
            logger.warning("start_at in state %s — ignoring", self._state)
            return

        # Launch iperf3 in a background thread so heartbeat keeps running
        t = threading.Thread(
            target=self._run_iperf3_thread,
            args=(payload,),
            daemon=True,
        )
        t.start()

    def _run_iperf3_thread(self, payload: dict) -> None:
        run_id = payload.get("run_id", self._current_run_id)
        start_ms = payload.get("start_unix_ms", 0)
        duration_sec = payload.get("duration_sec", 30)

        logger.info("Waiting until start (T=%d)", start_ms)
        actual_start_ms = sleep_until(start_ms)
        sync_offset_ms = actual_start_ms - start_ms
        logger.info("iperf3 starting. sync_offset_ms=%d", sync_offset_ms)

        self._set_state("RUNNING")

        # Publish each 1-second sample to MQTT for real-time Grafana streaming
        def _on_sample(sample):
            self._bus.publish(
                f"atf/agent/{self.agent_id}/live/{run_id}",
                {
                    "agent_id": self.agent_id,
                    "run_id": run_id,
                    "ts_ms": sample.ts_ms,
                    "throughput_mbps": sample.throughput_mbps,
                    "retransmits": sample.retransmits,
                },
                qos=0,
            )

        # Run iperf3
        cfg = self._traffic_config or {}
        result = run_iperf3(
            server=cfg.get("server", "localhost"),
            port=cfg.get("port", 5201),
            duration=duration_sec,
            protocol="udp" if cfg.get("type", "").endswith("udp") else "tcp",
            bandwidth_mbps=cfg.get("bandwidth_mbps"),
            parallel=cfg.get("parallel", 1),
            on_sample=_on_sample,
        )

        self._set_state("REPORTING")

        # Publish result
        if result.ok:
            logger.info(
                "iperf3 done: %.1f Mbps avg, %d retransmits",
                result.throughput_mean_mbps,
                result.total_retransmits,
            )
            self._bus.publish(
                f"atf/agent/{self.agent_id}/result/{run_id}",
                {
                    "run_id": run_id,
                    "agent_id": self.agent_id,
                    "status": "complete",
                    "actual_start_ms": actual_start_ms,
                    "sync_offset_ms": sync_offset_ms,
                    "summary": {
                        "throughput_mean_mbps": result.throughput_mean_mbps,
                        "throughput_stdev_mbps": result.throughput_stdev_mbps,
                        "throughput_p95_mbps": result.throughput_p95_mbps,
                        "total_retransmits": result.total_retransmits,
                        "lost_pct": result.lost_pct,
                    },
                    "samples": [
                        {
                            "ts_ms": s.ts_ms,
                            "throughput_mbps": s.throughput_mbps,
                            "retransmits": s.retransmits,
                        }
                        for s in result.samples
                    ],
                },
                qos=1,
            )
        else:
            logger.error("iperf3 failed: %s", result.error)
            self._bus.publish(
                f"atf/agent/{self.agent_id}/result/{run_id}",
                {
                    "run_id": run_id,
                    "agent_id": self.agent_id,
                    "status": "error",
                    "error": result.error,
                },
                qos=1,
            )

        self._current_run_id = None
        self._traffic_config = None
        self._set_state("IDLE")

    def _handle_stop(self, payload: dict) -> None:
        # RUNNING state is handled by _run_iperf3_thread finishing naturally
        if self._state == "ARMED":
            self._set_state("IDLE")

    # ------------------------------------------------------------------
    # NTP wait
    # ------------------------------------------------------------------

    def _wait_for_ntp(self, timeout: int) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._platform.is_ntp_synced():
                logger.info("NTP synced. offset=%.1f ms", self._platform.get_ntp_offset_ms() or 0)
                return
            logger.info("Waiting for NTP sync...")
            time.sleep(2)
        logger.warning("NTP sync timeout after %ds — continuing", timeout)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ATF Agent")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--agent-id", default=f"agent-{platform.node()}")
    args = parser.parse_args()

    agent = ATFAgent(args.broker, args.port, args.agent_id)

    def _shutdown(sig, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    agent.start()


if __name__ == "__main__":
    main()
