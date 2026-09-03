"""Enhanced state management with agent coordination."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import json
import pathlib
import uuid

@dataclass
class RunState:
    """Persistent shared context for an AgentTeams run."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task: Dict[str, Any] = field(default_factory=dict)
    status: str = "received"
    outputs: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    pending_approval: Optional[Dict[str, Any]] = None
    agent_states: Dict[str, str] = field(default_factory=dict)  # agent_name -> status
    evidence_ids: List[str] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def event(self, actor: str, action: str, payload: Optional[Dict[str, Any]] = None):
        """Record an event in the timeline."""
        self.events.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "payload": payload or {}
        })

    def set_agent_status(self, agent: str, status: str):
        """Update agent status."""
        self.agent_states[agent] = status
        self.event(agent, "status_changed", {"status": status})

    def record_error(self, agent: str, error: str, details: Optional[Dict[str, Any]] = None):
        """Record an error."""
        error_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "error": error,
            "details": details or {}
        }
        self.errors.append(error_entry)
        self.event(agent, "error_occurred", error_entry)

    def add_evidence(self, evidence_id: str):
        """Add an evidence reference."""
        self.evidence_ids.append(evidence_id)

    def save(self, path: pathlib.Path):
        """Save state to disk."""
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, path: pathlib.Path) -> "RunState":
        """Load state from disk."""
        data = json.loads(path.read_text())
        return cls(**data)

    # Bookkeeping fields that carry no information for the next agent.
    _INTERNAL_KEYS = ("raw_response", "attempts", "parse_note", "agent")

    def get_context_for_agent(self, agent: str) -> Dict[str, Any]:
        """Get relevant context for a specific agent.

        Upstream outputs are passed as their parsed structure only. The raw
        response text and per-attempt bookkeeping are stripped: they would
        double every upstream payload in the prompt and say nothing the parsed
        fields don't already say.
        """
        outputs = {
            name: {k: v for k, v in out.items() if k not in self._INTERNAL_KEYS}
            for name, out in self.outputs.items()
        }
        return {
            "run_id": self.run_id,
            "task": self.task,
            "status": self.status,
            "outputs": outputs,
            "agent_states": self.agent_states,
            "recent_events": self.events[-10:],  # Last 10 events
            "evidence_ids": self.evidence_ids,
        }
