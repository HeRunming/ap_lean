"""Configuration management."""
import os
import re
from pathlib import Path
from typing import Optional
import yaml

class Config:
    """Global configuration."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path or "config/config.yaml")
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration from file."""
        if self.config_path.exists():
            with open(self.config_path) as f:
                return yaml.safe_load(f) or {}
        return {}

    @property
    def anthropic_api_key(self) -> str:
        """Get Anthropic API key."""
        return os.getenv("ANTHROPIC_API_KEY", "")

    @property
    def anthropic_base_url(self) -> str:
        """Get Anthropic base URL."""
        return os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    @property
    def claude_model(self) -> str:
        """Get Claude model."""
        return self.config.get("claude", {}).get("model", "claude-opus-5")

    @property
    def max_tokens(self) -> int:
        """Get max tokens."""
        return self.config.get("claude", {}).get("max_tokens", 4096)

    @property
    def temperature(self) -> float:
        """Get temperature."""
        return self.config.get("claude", {}).get("temperature", 1.0)

    @property
    def evidence_dir(self) -> str:
        """Get evidence directory."""
        return self.config.get("evidence_dir", "evidence")

    @property
    def max_retries(self) -> int:
        """Get max retries for agent execution."""
        return self.config.get("execution", {}).get("max_retries", 3)

    @property
    def retry_delay(self) -> float:
        """Get retry delay in seconds."""
        return self.config.get("execution", {}).get("retry_delay", 2.0)

    @property
    def timeout(self) -> float:
        """Get HTTP read timeout in seconds for one agent call."""
        return float(self.config.get("execution", {}).get("timeout", 900))

    @property
    def max_context_chars(self) -> int:
        """Cap on upstream context injected into one agent prompt."""
        return int(self.config.get("execution", {}).get("max_context_chars", 60000))

    @property
    def max_repair_rounds(self) -> int:
        """How many Validator-fail → PipelineBuilder repair rounds to attempt."""
        return int(self.config.get("execution", {}).get("max_repair_rounds", 2))

    def get_agent_config(self, agent_name: str) -> dict:
        """Get configuration for a specific agent.

        Accepts either the CamelCase agent name used by the orchestrator
        (``PipelineBuilder``) or the snake_case key written in config.yaml
        (``pipeline_builder``).
        """
        agents = self.config.get("agents", {})
        if agent_name in agents:
            return agents[agent_name]
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", agent_name).lower()
        return agents.get(snake, {})
