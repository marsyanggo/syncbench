import time
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class AgentState:
    agent_id: str
    state: str = "UNKNOWN"
    platform: str = "unknown"
    agent_version: str = "unknown"
    ntp_offset_ms: float | None = None
    ntp_synced: bool = False
    band: str = "unknown"
    last_seen: float = field(default_factory=time.time)

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_seen) < 5.0

    @property
    def status_icon(self) -> str:
        return "●" if self.is_online else "○"

    @property
    def ntp_display(self) -> str:
        if self.ntp_offset_ms is None:
            return "—"
        return f"{self.ntp_offset_ms:+.1f} ms"


class InspectorState:
    def __init__(self) -> None:
        self._agents: dict[str, AgentState] = {}
        self._lock = Lock()

    def update_heartbeat(self, agent_id: str, payload: dict) -> None:
        with self._lock:
            a = self._agents.setdefault(agent_id, AgentState(agent_id=agent_id))
            a.state = payload.get("state", a.state)
            a.ntp_offset_ms = payload.get("ntp_offset_ms")
            a.ntp_synced = payload.get("ntp_synced", False)
            a.band = payload.get("band", a.band)
            a.last_seen = time.time()

    def update_status(self, agent_id: str, payload: dict) -> None:
        with self._lock:
            a = self._agents.setdefault(agent_id, AgentState(agent_id=agent_id))
            a.platform = payload.get("platform", a.platform)
            a.agent_version = payload.get("agent_version", a.agent_version)
            # Only update state from status if it's an explicit OFFLINE signal
            # (LWT). For online states, rely on heartbeat to keep last_seen fresh.
            state = payload.get("state", "")
            if state == "OFFLINE":
                a.state = "OFFLINE"
                a.last_seen = 0.0  # force is_online → False immediately
            elif state:
                a.state = state

    def all_agents(self) -> list[AgentState]:
        with self._lock:
            return sorted(self._agents.values(), key=lambda a: a.agent_id)
