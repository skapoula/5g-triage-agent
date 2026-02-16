"""Configuration management for TriageAgent."""

from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings


class TriageAgentConfig(BaseSettings):
    """Configuration loaded from environment variables."""

    # Memgraph
    memgraph_host: str = "localhost"
    memgraph_port: int = 7687

    # MCP Server URLs
    prometheus_url: str = "http://kube-prom-kube-prometheus-prometheus.monitoring:9090"
    loki_url: str = "http://loki.monitoring:3100"
    mcp_timeout: float = 3.0

    # LLM Configuration
    llm_api_key: str = ""  # Required in production
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 30

    # Observability
    langsmith_project: str = "5g-triage-agent"
    langsmith_api_key: str = ""

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
    }

    @field_validator("memgraph_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port is positive."""
        if v <= 0:
            raise ValueError("memgraph_port must be positive")
        return v

    @field_validator("prometheus_url", "loki_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL starts with http:// or https://."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @property
    def memgraph_uri(self) -> str:
        """Bolt connection URI for Memgraph."""
        return f"bolt://{self.memgraph_host}:{self.memgraph_port}"


@lru_cache(maxsize=1)
def get_config() -> TriageAgentConfig:
    """Get singleton configuration instance."""
    return TriageAgentConfig()


def get_config_dict() -> dict[str, Any]:
    """Get configuration as dictionary (for testing)."""
    return get_config().model_dump()
