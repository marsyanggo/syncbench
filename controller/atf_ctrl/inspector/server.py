"""ATF Inspector — real-time agent status dashboard.

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
from fastapi.responses import HTMLResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from controller.atf_ctrl.inspector.state import InspectorState
from shared.mqtt_bus import MQTTBus

logger = logging.getLogger("atf.inspector")

_state = InspectorState()
_update_event = asyncio.Event()
_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    yield


_app = FastAPI(title="ATF Inspector", lifespan=_lifespan)

# Jinja2 env
import pathlib
_TEMPLATES = pathlib.Path(__file__).parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html"]),
)


# ------------------------------------------------------------------
# Routes
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
            "ntp_offset_ms": a.ntp_offset_ms,
            "ntp_synced": a.ntp_synced,
            "ntp_display": a.ntp_display,
            "status_icon": a.status_icon,
        }
        for a in agents
    ]


@_app.get("/events")
async def sse_events():
    """Server-Sent Events — pushes full agent list on every state change."""

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
                        "ntp_display": a.ntp_display,
                        "status_icon": a.status_icon,
                    }
                    for a in agents
                ]
            )
            yield f"data: {data}\n\n"
            # Wait for next update (or 2s timeout for online/offline flap)
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
# MQTT subscriber (runs in background thread)
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

    bus.subscribe("atf/agent/+/heartbeat", _on_heartbeat, qos=0)
    bus.subscribe("atf/agent/+/status", _on_status, qos=1)
    logger.info("MQTT subscriber connected to %s:%d", broker, port)
    bus.loop_forever()


def _notify() -> None:
    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_update_event.set)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    global _loop

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

    # Start MQTT in background thread
    t = threading.Thread(target=_start_mqtt, args=(args.broker, args.port), daemon=True)
    t.start()

    logger.info("Inspector UI → http://localhost:%d", args.ui_port)
    uvicorn.run(_app, host="0.0.0.0", port=args.ui_port, log_level="warning")


if __name__ == "__main__":
    main()
