"""ATF Inspector — real-time agent status dashboard + run API.

Usage:
    uv run atf-inspector --broker localhost --port 1883
    open http://localhost:8080
"""

import argparse
import asyncio
import json
import logging
import re
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from controller.atf_ctrl.inspector.state import InspectorState
from shared.mqtt_bus import MQTTBus

logger = logging.getLogger("atf.inspector")

_state = InspectorState()
_update_event = asyncio.Event()
_loop: asyncio.AbstractEventLoop | None = None

# broker config — set at startup, used by /api/run
_broker_host = "localhost"
_broker_port = 1883

# active run queues: run_id → asyncio.Queue of events
_run_queues: dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    yield


_app = FastAPI(title="ATF Inspector", lifespan=_lifespan)

import pathlib
_TEMPLATES = pathlib.Path(__file__).parent / "templates"
from jinja2 import Environment, FileSystemLoader, select_autoescape
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html"]),
)


# ------------------------------------------------------------------
# Existing routes
# ------------------------------------------------------------------

@_app.get("/", response_class=HTMLResponse)
async def index():
    tmpl = _jinja.get_template("index.html")
    return tmpl.render(agents=_state.all_agents())


@_app.get("/api/state")
async def api_state():
    agents = _state.all_agents()
    return [
        {
            "agent_id": a.agent_id,
            "state": a.state,
            "online": a.is_online,
            "platform": a.platform,
            "band": a.band,
            "ntp_offset_ms": a.ntp_offset_ms,
            "ntp_synced": a.ntp_synced,
            "ntp_display": a.ntp_display,
            "status_icon": a.status_icon,
        }
        for a in agents
    ]


@_app.get("/events")
async def sse_events():
    """SSE — pushes full agent list on every state change."""

    async def generator():
        while True:
            agents = _state.all_agents()
            data = json.dumps(
                [
                    {
                        "agent_id": a.agent_id,
                        "state": a.state,
                        "online": a.is_online,
                        "platform": a.platform,
                        "band": a.band,
                        "ip": a.ip,
                        "ntp_display": a.ntp_display,
                        "status_icon": a.status_icon,
                    }
                    for a in agents
                ]
            )
            yield f"data: {data}\n\n"
            try:
                await asyncio.wait_for(
                    asyncio.shield(_update_event.wait()), timeout=2.0
                )
                _update_event.clear()
            except asyncio.TimeoutError:
                pass

    return StreamingResponse(generator(), media_type="text/event-stream")


@_app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ------------------------------------------------------------------
# Run API — Step 2
# ------------------------------------------------------------------

class RunRequest(BaseModel):
    agents: list[str]
    duration: int = 60
    server: str = "atf-broker.local"
    directions: dict[str, str] = {}  # agent_id → 'uplink'|'downlink'|'bidirectional'


@_app.post("/api/run")
async def api_start_run(req: RunRequest):
    """Start a run with the given agents. Returns run_id immediately."""
    from controller.atf_ctrl.scenarios.models import (
        Scenario, StationConfig, TrafficConfig, PreflightConfig,
    )
    from controller.atf_ctrl.orchestrator import Orchestrator

    # Validate agents are online
    online = {a.agent_id for a in _state.all_agents() if a.is_online}
    missing = [a for a in req.agents if a not in online]
    if missing:
        return JSONResponse({"error": f"Agents not online: {missing}"}, status_code=400)

    scenario = Scenario(
        name=f"UI Run ({len(req.agents)} STA)",
        duration_sec=req.duration,
        preflight=PreflightConfig(expected_agents=req.agents),
        stations=[
            StationConfig(node=a, traffic=TrafficConfig(
                type="iperf3_tcp",
                server=req.server,
                direction=req.directions.get(a, "uplink"),
            ))
            for a in req.agents
        ],
    )

    queue: asyncio.Queue = asyncio.Queue()
    run_id_holder: dict = {}
    run_id_ready = threading.Event()

    def on_event(event: str, data: dict) -> None:
        if event == "started":
            rid = data["run_id"]
            run_id_holder["run_id"] = rid
            _run_queues[rid] = queue
            run_id_ready.set()
        if _loop and _loop.is_running():
            _loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": data})

    def _run_thread() -> None:
        orc = Orchestrator(broker=_broker_host, port=_broker_port)
        try:
            orc.run(scenario, on_event=on_event)
        except Exception as exc:
            logger.error("Orchestrator error: %s", exc)
            if _loop and _loop.is_running():
                _loop.call_soon_threadsafe(
                    queue.put_nowait, {"event": "error", "data": {"error": str(exc)}}
                )
        finally:
            orc.stop()

    threading.Thread(target=_run_thread, daemon=True).start()

    # Wait for run_id (near-instant — just waiting for ULID generation)
    await asyncio.to_thread(run_id_ready.wait, 5.0)
    run_id = run_id_holder.get("run_id")
    if not run_id:
        return JSONResponse({"error": "Failed to start run"}, status_code=500)

    return {"run_id": run_id}


@_app.get("/api/run/{run_id}/stream")
async def api_run_stream(run_id: str):
    """SSE stream of run events: started / phase / sample / done / error."""
    queue = _run_queues.get(run_id)
    if queue is None:
        return JSONResponse({"error": "run not found"}, status_code=404)

    async def generator():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event["event"] in ("done", "error"):
                    _run_queues.pop(run_id, None)
                    break
            except asyncio.TimeoutError:
                yield "data: {\"event\": \"heartbeat\"}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


@_app.get("/api/metrics/{run_id}")
async def api_metrics(run_id: str):
    """Return throughput time-series for a completed run from InfluxDB."""
    try:
        from influxdb_client import InfluxDBClient
        from controller.atf_ctrl.metrics.influx_writer import (
            INFLUX_TOKEN, INFLUX_ORG, INFLUX_URL, INFLUX_BUCKET,
        )

        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        query_api = client.query_api()
        query = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "throughput" and r.run_id == "{run_id}")
  |> filter(fn: (r) => r._field == "throughput_mbps")
  |> sort(columns: ["_time"])
'''
        tables = query_api.query(query)
        result: dict[str, list] = {}
        for table in tables:
            for record in table.records:
                agent_id = record.values.get("agent_id", "unknown")
                if agent_id not in result:
                    result[agent_id] = []
                result[agent_id].append({
                    "ts_ms": int(record.get_time().timestamp() * 1000),
                    "throughput_mbps": record.get_value(),
                })
        client.close()
        return result
    except Exception as exc:
        logger.warning("Metrics query failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------
# MQTT subscriber (background thread)
# ------------------------------------------------------------------

def _start_mqtt(broker: str, port: int) -> None:
    bus = MQTTBus()
    bus.connect(broker, port, client_id="atf-inspector")

    def _on_heartbeat(topic: str, payload: dict) -> None:
        m = re.match(r"atf/agent/([^/]+)/heartbeat", topic)
        if m:
            _state.update_heartbeat(m.group(1), payload)
            _notify()

    def _on_status(topic: str, payload: dict) -> None:
        m = re.match(r"atf/agent/([^/]+)/status", topic)
        if m:
            _state.update_status(m.group(1), payload)
            _notify()

    def _on_live(topic: str, payload: dict) -> None:
        # topic: atf/agent/{id}/live/{run_id}
        parts = topic.split("/")
        if len(parts) == 5:
            agent_id, run_id = parts[2], parts[4]
            queue = _run_queues.get(run_id)
            if queue and _loop and _loop.is_running():
                _loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {
                        "event": "sample",
                        "data": {
                            "agent_id": agent_id,
                            "run_id": run_id,
                            "throughput_mbps": payload.get("throughput_mbps"),
                            "ts_ms": payload.get("ts_ms"),
                        },
                    },
                )

    bus.subscribe("atf/agent/+/heartbeat", _on_heartbeat, qos=0)
    bus.subscribe("atf/agent/+/status", _on_status, qos=1)
    bus.subscribe("atf/agent/+/live/+", _on_live, qos=0)
    logger.info("MQTT subscriber connected to %s:%d", broker, port)
    bus.loop_forever()


def _notify() -> None:
    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_update_event.set)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    global _broker_host, _broker_port

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="ATF Inspector UI")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--ui-port", type=int, default=8080)
    args = parser.parse_args()

    _broker_host = args.broker
    _broker_port = args.port

    t = threading.Thread(target=_start_mqtt, args=(args.broker, args.port), daemon=True)
    t.start()

    logger.info("Inspector UI → http://localhost:%d", args.ui_port)
    uvicorn.run(_app, host="0.0.0.0", port=args.ui_port, log_level="warning")


if __name__ == "__main__":
    main()
