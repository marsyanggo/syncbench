"""ATF Agent — entry point and state machine.

States:  BOOT → IDLE → PREPARING → ARMED → RUNNING → REPORTING → IDLE
         Any state → ERROR (on fatal failure)
         Any state → OFFLINE (LWT, on unexpected disconnect)

Usage:
    uv run atf-agent --broker localhost --port 1883 --agent-id rpi-sta-01
    # or via Docker on RPi:
    docker run atf-agent --broker 192.168.1.10 --agent-id rpi-sta-01
"""

import argparse
import logging
import platform
import signal
import sys
import time

from shared.mqtt_bus import MQTTBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("atf.agent")

VERSION = "0.1.0"
HEARTBEAT_INTERVAL = 1.0  # seconds


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
            self._broker,
            self._port,
            client_id=self.agent_id,
            lwt_topic=self._status_topic,
            lwt_payload={"state": "OFFLINE", "agent_id": self.agent_id},
        )
        self._bus.loop_start()

        self._subscribe()
        self._set_state("BOOT")

        # Wait for NTP sync before going IDLE (max 30s)
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
    # State management
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
            },
            qos=1,
            retain=True,
        )

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    def _run_heartbeat_loop(self) -> None:
        logger.info("Heartbeat loop started (%.1fs interval)", HEARTBEAT_INTERVAL)
        while self._running:
            ntp_offset = self._platform.get_ntp_offset_ms()
            self._bus.publish(
                self._heartbeat_topic,
                {
                    "agent_id": self.agent_id,
                    "state": self._state,
                    "ntp_offset_ms": ntp_offset,
                    "ntp_synced": self._platform.is_ntp_synced(),
                },
                qos=0,
            )
            time.sleep(HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        self._bus.subscribe(
            "atf/ctrl/broadcast/+",
            self._on_broadcast,
            qos=1,
        )
        self._bus.subscribe(
            f"atf/ctrl/unicast/agent/{self.agent_id}/+",
            self._on_unicast,
            qos=1,
        )

    def _on_broadcast(self, topic: str, payload: dict) -> None:
        cmd = topic.split("/")[-1]
        logger.info("Broadcast: %s", cmd)
        if cmd == "prepare":
            self._handle_prepare(payload)
        elif cmd == "start_at":
            self._handle_start_at(payload)
        elif cmd == "stop":
            self._handle_stop(payload)
        elif cmd == "teardown":
            self._set_state("IDLE")

    def _on_unicast(self, topic: str, payload: dict) -> None:
        cmd = topic.split("/")[-1]
        logger.info("Unicast: %s", cmd)

    def _handle_prepare(self, payload: dict) -> None:
        if self._state != "IDLE":
            logger.warning("prepare received in state %s — ignoring", self._state)
            return
        self._set_state("PREPARING")
        run_id = payload.get("run_id", "unknown")
        # Acknowledge
        self._bus.publish(
            f"atf/agent/{self.agent_id}/ack/{payload.get('msg_id', 'unknown')}",
            {"run_id": run_id, "agent_id": self.agent_id, "ok": True},
        )
        self._set_state("ARMED")

    def _handle_start_at(self, payload: dict) -> None:
        if self._state != "ARMED":
            logger.warning("start_at received in state %s — ignoring", self._state)
            return
        start_ms = payload.get("start_unix_ms", 0)
        now_ms = int(time.time() * 1000)
        delta_ms = start_ms - now_ms
        if delta_ms > 0:
            logger.info("Sleeping %.0f ms until start", delta_ms)
            time.sleep(delta_ms / 1000)
        actual_start_ms = int(time.time() * 1000)
        sync_offset_ms = actual_start_ms - start_ms
        logger.info("Started. sync_offset_ms=%d", sync_offset_ms)
        self._set_state("RUNNING")

    def _handle_stop(self, payload: dict) -> None:
        if self._state in ("RUNNING", "ARMED"):
            self._set_state("REPORTING")
            # TODO: collect and publish results (Week 2)
            self._set_state("IDLE")

    # ------------------------------------------------------------------
    # NTP wait
    # ------------------------------------------------------------------

    def _wait_for_ntp(self, timeout: int) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._platform.is_ntp_synced():
                offset = self._platform.get_ntp_offset_ms()
                logger.info("NTP synced. offset=%.1f ms", offset or 0)
                return
            logger.info("Waiting for NTP sync...")
            time.sleep(2)
        logger.warning("NTP sync timeout after %ds — continuing anyway", timeout)


# ------------------------------------------------------------------
# CLI entry point
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
