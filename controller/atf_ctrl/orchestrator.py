"""ATF Test Orchestrator — runs a scenario end-to-end.

Flow:
    prepare (broadcast)
        └─ wait for acks from all expected agents
    start_at (broadcast, T+5s)
        └─ agents sleep until T, run iperf3
    wait (duration + buffer)
    stop (broadcast)
    collect results
"""

import logging
import threading
import time
from dataclasses import dataclass, field

from ulid import ULID

from controller.atf_ctrl.scenarios.models import Scenario
from shared.mqtt_bus import MQTTBus

logger = logging.getLogger("atf.orchestrator")

ACK_TIMEOUT = 30.0      # seconds to wait for all agents to ack prepare
RESULT_TIMEOUT = 30.0   # seconds to wait for results after stop
START_DELAY_MS = 5000   # ms between start_at broadcast and actual start


@dataclass
class AgentResult:
    agent_id: str
    status: str = "pending"
    sync_offset_ms: int | None = None
    throughput_mean_mbps: float | None = None
    throughput_stdev_mbps: float | None = None
    total_retransmits: int | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RunResult:
    run_id: str
    scenario_name: str
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(
            r.status == "complete" for r in self.agent_results.values()
        )


class Orchestrator:
    def __init__(self, broker: str = "localhost", port: int = 1883) -> None:
        self._bus = MQTTBus()
        self._bus.connect(broker, port, client_id="atf-controller")
        self._bus.loop_start()
        self._acks: dict[str, threading.Event] = {}
        self._results: dict[str, dict] = {}
        self._result_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, scenario: Scenario) -> RunResult:
        run_id = f"run-{ULID()}"
        result = RunResult(run_id=run_id, scenario_name=scenario.name)
        expected = [s.node for s in scenario.stations]

        logger.info("Starting run %s — scenario: %s", run_id, scenario.name)
        logger.info("Expected agents: %s", expected)

        # Subscribe to acks and results for this run
        cmd_id = str(ULID())
        self._setup_subscriptions(run_id, expected)

        # Phase 1: Prepare
        logger.info("Phase 1: Broadcasting prepare")
        self._bus.publish(
            "atf/ctrl/broadcast/prepare",
            {
                "run_id": run_id,
                "cmd_id": cmd_id,
                "scenario_name": scenario.name,
                "expected_agents": expected,
                "phase_timeout_sec": int(ACK_TIMEOUT),
                # Pass iperf3 config per-station (agent picks its own)
                "station_traffic": {
                    s.node: {
                        "type": s.traffic.type,
                        "server": s.traffic.server,
                        "port": s.traffic.port,
                        "parallel": s.traffic.parallel,
                        "bandwidth_mbps": s.traffic.bandwidth_mbps,
                    }
                    for s in scenario.stations
                },
            },
        )

        if not self._wait_for_acks(expected, cmd_id, timeout=ACK_TIMEOUT):
            missing = [a for a in expected if not self._acks.get(a, threading.Event()).is_set()]
            result.error = f"Prepare timeout — no ack from: {missing}"
            logger.error(result.error)
            return result

        # Phase 2: Start
        start_ms = int(time.time() * 1000) + START_DELAY_MS
        logger.info("Phase 2: Broadcasting start_at (T+%dms)", START_DELAY_MS)
        self._bus.publish(
            "atf/ctrl/broadcast/start_at",
            {
                "run_id": run_id,
                "start_unix_ms": start_ms,
                "duration_sec": scenario.duration_sec,
                "warmup_sec": scenario.warmup_sec,
            },
        )

        # Phase 3: Wait
        wait_sec = scenario.duration_sec + START_DELAY_MS / 1000 + 5
        logger.info("Phase 3: Waiting %.0fs for test to complete", wait_sec)
        time.sleep(wait_sec)

        # Phase 4: Stop
        logger.info("Phase 4: Broadcasting stop")
        self._bus.publish(
            "atf/ctrl/broadcast/stop",
            {"run_id": run_id, "reason": "normal_complete"},
        )

        # Phase 5: Collect results
        logger.info("Phase 5: Collecting results (timeout=%ds)", int(RESULT_TIMEOUT))
        result.agent_results = self._collect_results(run_id, expected, timeout=RESULT_TIMEOUT)

        # Teardown
        self._bus.publish("atf/ctrl/broadcast/teardown", {"run_id": run_id})
        logger.info("Run %s finished. ok=%s", run_id, result.ok)
        return result

    def stop(self) -> None:
        self._bus.loop_stop()
        self._bus.disconnect()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _setup_subscriptions(self, run_id: str, agents: list[str]) -> None:
        # Acks
        for agent_id in agents:
            self._acks[agent_id] = threading.Event()

        def _on_ack(topic: str, payload: dict) -> None:
            agent_id = payload.get("agent_id")
            if agent_id and agent_id in self._acks:
                logger.info("Ack received from %s", agent_id)
                self._acks[agent_id].set()

        self._bus.subscribe(f"atf/agent/+/ack/+", _on_ack, qos=1)

        # Results
        def _on_result(topic: str, payload: dict) -> None:
            parts = topic.split("/")  # atf/agent/{id}/result/{run_id}
            if len(parts) == 5 and parts[4] == run_id:
                agent_id = parts[2]
                with self._result_lock:
                    self._results[agent_id] = payload
                logger.info("Result received from %s", agent_id)

        self._bus.subscribe(f"atf/agent/+/result/{run_id}", _on_result, qos=1)

    def _wait_for_acks(
        self, agents: list[str], cmd_id: str, timeout: float
    ) -> bool:
        deadline = time.time() + timeout
        for agent_id in agents:
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            event = self._acks.get(agent_id, threading.Event())
            if not event.wait(timeout=remaining):
                logger.warning("Ack timeout for %s", agent_id)
                return False
        return True

    def _collect_results(
        self, run_id: str, agents: list[str], timeout: float
    ) -> dict[str, AgentResult]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._result_lock:
                if all(a in self._results for a in agents):
                    break
            time.sleep(0.5)

        results = {}
        with self._result_lock:
            for agent_id in agents:
                raw = self._results.get(agent_id)
                if raw is None:
                    results[agent_id] = AgentResult(
                        agent_id=agent_id,
                        status="timeout",
                        error="No result received within timeout",
                    )
                else:
                    summary = raw.get("summary", {})
                    results[agent_id] = AgentResult(
                        agent_id=agent_id,
                        status=raw.get("status", "unknown"),
                        sync_offset_ms=raw.get("sync_offset_ms"),
                        throughput_mean_mbps=summary.get("throughput_mean_mbps"),
                        throughput_stdev_mbps=summary.get("throughput_stdev_mbps"),
                        total_retransmits=summary.get("total_retransmits"),
                        error=raw.get("error"),
                        raw=raw,
                    )
        return results
