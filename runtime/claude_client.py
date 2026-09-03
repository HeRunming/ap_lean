"""Claude API client for agent execution."""
import os
import json
import httpx
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

@dataclass
class Message:
    role: str
    content: str

@dataclass
class ClaudeResponse:
    content: str
    usage: Dict[str, int]
    model: str
    stop_reason: str
    raw: Dict[str, Any]

class ClaudeClient:
    """Claude API client with streaming and retry support."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "claude-opus-5",
        max_tokens: int = 4096,
        temperature: float = 1.0,
        timeout: float = 900.0,
    ):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    def create_message(
        self,
        messages: List[Message],
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
    ) -> ClaudeResponse:
        """Create a non-streaming message. Per-call model/max_tokens override the defaults."""
        payload = {
            "model": model or self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

        if system:
            payload["system"] = system

        response = self.client.post("/v1/messages", json=payload)
        response.raise_for_status()

        data = response.json()
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        return ClaudeResponse(
            content=content,
            usage=data.get("usage", {}),
            model=data.get("model", ""),
            stop_reason=data.get("stop_reason", ""),
            raw=data,
        )

    def close(self):
        """Close the HTTP client."""
        self.client.close()

class AgentSession:
    """Manages a conversational session for one agent."""

    def __init__(
        self,
        agent_name: str,
        system_prompt: str,
        client: ClaudeClient,
    ):
        self.agent_name = agent_name
        self.system_prompt = system_prompt
        self.client = client
        self.messages: List[Message] = []

    def send(
        self,
        user_message: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> ClaudeResponse:
        """Send a message and return the full response.

        An empty body is not appended to the history, so a retry re-asks the same
        turn instead of building on a blank assistant message.
        """
        self.messages.append(Message(role="user", content=user_message))

        response = self.client.create_message(
            messages=self.messages,
            system=self.system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if response.content.strip():
            self.messages.append(Message(role="assistant", content=response.content))
        else:
            self.messages.pop()

        return response

    def get_history(self) -> List[Dict[str, str]]:
        """Get conversation history."""
        return [{"role": m.role, "content": m.content} for m in self.messages]
