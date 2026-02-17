"""Configuration management for TriageAgent."""

from functools import lru_cache
from typing import Any, Literal

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
    llm_model: str = "qwen3-4b-instruct-2507.Q4_K_M.gguf"  # local default; override for openai/anthropic
    llm_timeout: int = 30
    llm_provider: Literal["openai", "anthropic", "local"] = "openai"
    # Env var: LLM_PROVIDER — selects LLM backend
    # "openai": ChatOpenAI using llm_api_key + llm_model
    # "anthropic": ChatAnthropic using llm_api_key + llm_model (requires langchain-anthropic)
    # "local": ChatOpenAI with base_url for in-cluster vLLM/Ollama, no external api_key needed
    llm_base_url: str = "http://qwen3-4b.ml-serving.svc.cluster.local/v1"
    # Env var: LLM_BASE_URL — OpenAI-compatible base URL for the local provider
    # Defaults to the in-cluster Qwen3-4b KServe ClusterIP service (port 80)
    # NodePort fallback (external): http://10.0.1.2:30080/v1

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
