"""Message bus for agent communication."""
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

@dataclass
class AgentMessage:
    """Message sent between agents."""
    msg_id: str
    from_agent: str
    to_agent: str
    content: Dict[str, Any]
    timestamp: str
    reply_to: Optional[str] = None

class MessageBus:
    """Simple in-memory message bus with persistence."""

    def __init__(self, evidence_dir: str = "evidence"):
        self.messages: List[AgentMessage] = []
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        from_agent: str,
        to_agent: str,
        content: Dict[str, Any],
        reply_to: Optional[str] = None,
    ) -> str:
        """Send a message from one agent to another."""
        import uuid
        msg_id = str(uuid.uuid4())

        msg = AgentMessage(
            msg_id=msg_id,
            from_agent=from_agent,
            to_agent=to_agent,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reply_to=reply_to,
        )

        self.messages.append(msg)
        return msg_id

    def get_messages_for(self, agent: str) -> List[AgentMessage]:
        """Get all messages sent to an agent."""
        return [m for m in self.messages if m.to_agent == agent]

    def get_latest_from(self, from_agent: str, to_agent: str) -> Optional[AgentMessage]:
        """Get the latest message from one agent to another."""
        msgs = [
            m for m in self.messages
            if m.from_agent == from_agent and m.to_agent == to_agent
        ]
        return msgs[-1] if msgs else None

    def save(self, run_id: str):
        """Persist messages to disk."""
        path = self.evidence_dir / f"{run_id}_messages.json"
        data = [asdict(m) for m in self.messages]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
