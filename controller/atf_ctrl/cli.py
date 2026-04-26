"""ATF CLI — atf-run and atf-status commands."""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def run() -> None:
    parser = argparse.ArgumentParser(
        prog="atf-run",
        description="Run an ATF test scenario",
    )
    parser.add_argument("scenario", help="Path to scenario YAML (e.g. scenarios/00_smoke_test.yaml)")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host (default: localhost)")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port (default: 1883)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress INFO logs")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    # Load scenario
    from controller.atf_ctrl.scenarios.loader import load
    try:
        scenario = load(args.scenario)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n▶  {scenario.name}")
    print(f"   agents : {[s.node for s in scenario.stations]}")
    print(f"   duration: {scenario.duration_sec}s")
    print()

    # Run
    from controller.atf_ctrl.orchestrator import Orchestrator
    orc = Orchestrator(broker=args.broker, port=args.port)
    try:
        result = orc.run(scenario)
    finally:
        orc.stop()

    # Print result
    _print_result(result)
    sys.exit(0 if result.ok else 1)


def status() -> None:
    parser = argparse.ArgumentParser(
        prog="atf-status",
        description="Show live agent status",
    )
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()

    import time
    from controller.atf_ctrl.inspector.state import InspectorState
    from shared.mqtt_bus import MQTTBus
    import re

    state = InspectorState()
    bus = MQTTBus()
    bus.connect(args.broker, args.port, client_id="atf-status-cli")

    def _on_heartbeat(topic: str, payload: dict) -> None:
        m = re.match(r"atf/agent/([^/]+)/heartbeat", topic)
        if m:
            state.update_heartbeat(m.group(1), payload)

    def _on_status(topic: str, payload: dict) -> None:
        m = re.match(r"atf/agent/([^/]+)/status", topic)
        if m:
            state.update_status(m.group(1), payload)

    bus.subscribe("atf/agent/+/heartbeat", _on_heartbeat, qos=0)
    bus.subscribe("atf/agent/+/status", _on_status, qos=1)
    bus.loop_start()

    print("Listening for agents (Ctrl-C to stop)...\n")
    try:
        while True:
            agents = state.all_agents()
            if agents:
                print(f"\r{' ' * 60}\r", end="")
                for a in agents:
                    icon = "●" if a.is_online else "○"
                    ntp = a.ntp_display
                    print(f"  {icon} {a.agent_id:<20} {a.state:<12} NTP: {ntp:<12} {a.platform}")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bus.loop_stop()
        bus.disconnect()


def _print_result(result) -> None:
    print("─" * 50)
    if result.error:
        print(f"✗  FAILED  {result.error}")
        return

    all_ok = result.ok
    print(f"{'✓' if all_ok else '✗'}  {'PASSED' if all_ok else 'FAILED'}  run_id: {result.run_id}")
    print()
    for agent_id, r in result.agent_results.items():
        if r.status == "complete":
            print(f"  {agent_id}")
            print(f"    throughput : {r.throughput_mean_mbps:.1f} Mbps avg  (±{r.throughput_stdev_mbps:.1f})")
            print(f"    retransmits: {r.total_retransmits}")
            print(f"    sync_offset: {r.sync_offset_ms} ms")
        else:
            print(f"  {agent_id}  ✗ {r.status}: {r.error}")
    print("─" * 50)
