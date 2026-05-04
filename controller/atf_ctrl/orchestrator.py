"""ATF Test Orchestrator — runs a scenario end-to-end.

Flow:
    prepare (broadcast)
        └─ wait for acks from all expected agents
    start_at (broadcast, T+5s)
        └─ agents sleep until T, run iperf3
    wait (duration + buffer)
    stop (broadcast)
    collect results

Can be called programmatically via run(scenario, on_event=cb) where cb receives
(event_type: str, data: dict) at each phase transition.
"""

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ulid import ULID

from controller.atf_ctrl.scenarios.models import Scenario
from shared.mqtt_bus import MQTTBus

logger = logging.getLogger("atf.orchestrator")

ACK_TIMEOUT = 30.0      # seconds to wait for all agents to ack prepare
RESULT_TIMEOUT = 30.0   # seconds to wait for results after stop
START_DELAY_MS = 5000   # ms between start_at broadcast and actual start
BASE_IPERF3_PORT = 5201


def _find_iperf3() -> str:
    for name in ["iperf3", "iperf3-darwin"]:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("iperf3 not found — install with: brew install iperf3 / apt install iperf3")


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
        self._iperf3_procs: list[subprocess.Popen] = []
        self._agent_ips: dict[str, str] = {}  # agent_id → IP (from heartbeat)
        self._bus.subscribe("atf/agent/+/heartbeat", self._on_agent_heartbeat, qos=0)

    def _on_agent_heartbeat(self, topic: str, payload: dict) -> None:
        agent_id = payload.get("agent_id")
        ip = payload.get("ip")
        if agent_id and ip:
            self._agent_ips[agent_id] = ip

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        scenario: Scenario,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> RunResult:
        def emit(event: str, data: dict) -> None:
            if on_event:
                try:
                    on_event(event, data)
                except Exception:
                    pass

        run_id = f"run-{ULID()}"
        result = RunResult(run_id=run_id, scenario_name=scenario.name)
        expected = [s.node for s in scenario.stations]

        logger.info("Starting run %s — scenario: %s", run_id, scenario.name)
        logger.info("Expected agents: %s", expected)
        directions = {s.node: s.traffic.direction for s in scenario.stations}
        emit("started", {"run_id": run_id, "agents": expected, "scenario": scenario.name, "directions": directions})

        # Assign unique ports and build station_traffic map
        station_traffic = {
            s.node: {
                "type": s.traffic.type,
                "server": s.traffic.server,
                "port": BASE_IPERF3_PORT + i,
                "parallel": s.traffic.parallel,
                "bandwidth_mbps": s.traffic.bandwidth_mbps,
                "direction": s.traffic.direction,
                "ac": s.traffic.ac,
            }
            for i, s in enumerate(scenario.stations)
        }

        # Subscribe to acks, results, and live samples for this run
        cmd_id = str(ULID())
        self._setup_subscriptions(run_id, expected)
        self._setup_live_subscription(run_id, scenario.name)

        # Start local iperf3 servers before prepare
        self._start_iperf3_servers(station_traffic)

        try:
            return self._run_phases(run_id, cmd_id, scenario, expected, station_traffic, result, emit)
        finally:
            self._stop_iperf3_servers()

    def _run_phases(
        self,
        run_id: str,
        cmd_id: str,
        scenario: Scenario,
        expected: list[str],
        station_traffic: dict,
        result: RunResult,
        emit: Callable[[str, dict], None],
    ) -> RunResult:
        # Phase 1: Prepare
        logger.info("Phase 1: Broadcasting prepare")
        emit("phase", {"phase": "preparing", "run_id": run_id})
        self._bus.publish(
            "atf/ctrl/broadcast/prepare",
            {
                "run_id": run_id,
                "cmd_id": cmd_id,
                "scenario_name": scenario.name,
                "expected_agents": expected,
                "phase_timeout_sec": int(ACK_TIMEOUT),
                "station_traffic": station_traffic,
            },
        )

        if not self._wait_for_acks(expected, cmd_id, timeout=ACK_TIMEOUT):
            missing = [a for a in expected if not self._acks.get(a, threading.Event()).is_set()]
            result.error = f"Prepare timeout — no ack from: {missing}"
            logger.error(result.error)
            self._bus.publish("atf/ctrl/broadcast/teardown", {"run_id": run_id})
            emit("error", {"run_id": run_id, "error": result.error})
            return result

        # Phase 2: Start
        start_ms = int(time.time() * 1000) + START_DELAY_MS
        logger.info("Phase 2: Broadcasting start_at (T+%dms)", START_DELAY_MS)
        emit("phase", {"phase": "running", "run_id": run_id, "duration_sec": scenario.duration_sec, "start_unix_ms": start_ms})
        self._bus.publish(
            "atf/ctrl/broadcast/start_at",
            {
                "run_id": run_id,
                "start_unix_ms": start_ms,
                "duration_sec": scenario.duration_sec,
                "warmup_sec": scenario.warmup_sec,
            },
        )

        # Spawn downlink clients after start_at.
        # Extra 1.5s grace period lets agents finish sleep_until + fork iperf3 server + bind port
        # before the Mac's client attempts to connect.
        time.sleep(START_DELAY_MS / 1000 + 1.5)
        self._start_iperf3_downlink_clients(
            station_traffic, self._agent_ips, scenario.duration_sec
        )

        # Phase 3: Wait — downlink already slept START_DELAY_MS, adjust accordingly
        has_downlink = any(
            v.get("direction", "uplink") == "downlink" for v in station_traffic.values()
        )
        # downlink: already slept START_DELAY + 1.5s in phase 2, subtract that from wait
        wait_sec = scenario.duration_sec + (0 if has_downlink else START_DELAY_MS / 1000) + 5 - (1.5 if has_downlink else 0)
        logger.info("Phase 3: Waiting %.0fs for test to complete", wait_sec)
        time.sleep(wait_sec)

        # Phase 4: Stop
        logger.info("Phase 4: Broadcasting stop")
        emit("phase", {"phase": "collecting", "run_id": run_id})
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

        # Write to InfluxDB
        try:
            from controller.atf_ctrl.metrics.influx_writer import InfluxWriter
            writer = InfluxWriter()
            writer.write_run(result, scenario.name)
            writer.close()
        except Exception as exc:
            logger.warning("InfluxDB write failed (non-fatal): %s", exc)

        # Emit final result
        emit("done", {
            "run_id": run_id,
            "ok": result.ok,
            "error": result.error,
            "agents": {
                aid: {
                    "status": r.status,
                    "throughput_mean_mbps": r.throughput_mean_mbps,
                    "throughput_stdev_mbps": r.throughput_stdev_mbps,
                    "total_retransmits": r.total_retransmits,
                    "sync_offset_ms": r.sync_offset_ms,
                    "error": r.error,
                }
                for aid, r in result.agent_results.items()
            },
        })

        return result

    def stop(self) -> None:
        self._bus.loop_stop()
        self._bus.disconnect()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _setup_live_subscription(self, run_id: str, scenario_name: str) -> None:
        try:
            from influxdb_client import InfluxDBClient, Point, WritePrecision
            from influxdb_client.client.write_api import SYNCHRONOUS
            from controller.atf_ctrl.metrics.influx_writer import INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET, INFLUX_URL

            client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = client.write_api(write_options=SYNCHRONOUS)

            def _on_live(topic: str, payload: dict) -> None:
                agent_id = topic.split("/")[2]
                p = (
                    Point("throughput")
                    .tag("run_id", run_id)
                    .tag("agent_id", agent_id)
                    .tag("scenario", scenario_name)
                    .field("throughput_mbps", float(payload.get("throughput_mbps", 0)))
                    .field("retransmits", int(payload.get("retransmits", 0)))
                    .time(payload["ts_ms"] * 1_000_000, WritePrecision.NS)
                )
                try:
                    write_api.write(bucket=INFLUX_BUCKET, record=p)
                except Exception as e:
                    logger.warning("InfluxDB live write failed (non-fatal): %s", e)

            self._bus.subscribe(f"atf/agent/+/live/{run_id}", _on_live, qos=0)
            logger.info("Live InfluxDB writer active for run %s", run_id)
        except Exception as exc:
            logger.warning("Live collector unavailable (non-fatal): %s", exc)

    def _start_iperf3_servers(self, station_traffic: dict) -> None:
        """For uplink/bidirectional: spawn iperf3 servers on Mac (one per port)."""
        binary = _find_iperf3()
        ports = sorted({
            v["port"] for v in station_traffic.values()
            if v.get("direction", "uplink") in ("uplink", "bidirectional")
        })
        for port in ports:
            proc = subprocess.Popen(
                [binary, "-s", "-p", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._iperf3_procs.append(proc)
            logger.info("iperf3 server started on port %d (pid %d)", port, proc.pid)

    def _start_iperf3_downlink_clients(
        self, station_traffic: dict, agent_ips: dict[str, str], duration_sec: int
    ) -> None:
        """For downlink: spawn iperf3 clients on Mac targeting each device's IP."""
        binary = _find_iperf3()
        for node, cfg in station_traffic.items():
            if cfg.get("direction", "uplink") != "downlink":
                continue
            ip = agent_ips.get(node)
            if not ip:
                logger.warning("No IP known for %s — skipping downlink client", node)
                continue
            _AC_TOS = {"vo": "0xb8", "vi": "0x68", "be": "0x00", "bk": "0x20"}
            tos = _AC_TOS.get(cfg.get("ac", "be"), "0x00")
            cmd = [binary, "--client", ip, "--port", str(cfg["port"]),
                   "--time", str(duration_sec), "--interval", "1", "--forceflush"]
            if tos != "0x00":
                cmd += ["--tos", tos]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._iperf3_procs.append(proc)
            logger.info("iperf3 downlink client → %s:%d ac=%s (pid %d)",
                        ip, cfg["port"], cfg.get("ac", "be"), proc.pid)

    def _stop_iperf3_servers(self) -> None:
        for proc in self._iperf3_procs:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._iperf3_procs.clear()
        logger.info("iperf3 servers stopped")

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
