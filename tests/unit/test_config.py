"""Tests for configuration module.

Test-first: these tests define the expected behavior of TriageAgentConfig
and get_config() before implementation. Every test isolates from environment
variables using patch.dict(clear=True) to prevent BaseSettings env leakage.
"""

import os
from unittest.mock import patch

import pytest

from triage_agent.config import TriageAgentConfig, get_config

# Keys that BaseSettings might read from the environment.
# We clear these so host-level env vars don't leak into default-value tests.
_CONFIG_ENV_KEYS = [
    "MEMGRAPH_HOST",
    "MEMGRAPH_PORT",
    "PROMETHEUS_URL",
    "LOKI_URL",
    "MCP_TIMEOUT",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TIMEOUT",
    "LANGSMITH_PROJECT",
    "LANGSMITH_API_KEY",
]

_CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in _CONFIG_ENV_KEYS}


class TestDefaultValues:
    """Default values are correct when no env vars are set."""

    def test_memgraph_defaults(self) -> None:
        """Memgraph host/port should default to localhost:7687."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key")

        assert config.memgraph_host == "localhost"
        assert config.memgraph_port == 7687

    def test_mcp_url_defaults(self) -> None:
        """Prometheus and Loki URLs should default to cluster-local addresses."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key")

        assert config.prometheus_url == "http://prometheus:9090"
        assert config.loki_url == "http://loki:3100"

    def test_mcp_timeout_default(self) -> None:
        """MCP timeout should default to 3.0 seconds."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key")

        assert config.mcp_timeout == 3.0

    def test_llm_defaults(self) -> None:
        """LLM model and timeout should have sensible defaults."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key")

        assert config.llm_model == "gpt-4o-mini"
        assert config.llm_timeout == 30

    def test_langsmith_default_project(self) -> None:
        """LangSmith project should default to '5g-triage-agent'."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key")

        assert config.langsmith_project == "5g-triage-agent"

    def test_llm_api_key_stored(self) -> None:
        """Explicitly passed llm_api_key should be stored."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="sk-test-123")

        assert config.llm_api_key == "sk-test-123"


class TestEnvironmentVariableOverride:
    """Environment variable override works for all fields."""

    def test_memgraph_host_from_env(self) -> None:
        """MEMGRAPH_HOST env var should override default."""
        with patch.dict(
            os.environ, {**_CLEAN_ENV, "MEMGRAPH_HOST": "mg.internal"}, clear=True
        ):
            config = TriageAgentConfig()

        assert config.memgraph_host == "mg.internal"

    def test_memgraph_port_from_env(self) -> None:
        """MEMGRAPH_PORT env var should override default and parse as int."""
        with patch.dict(
            os.environ, {**_CLEAN_ENV, "MEMGRAPH_PORT": "7700"}, clear=True
        ):
            config = TriageAgentConfig()

        assert config.memgraph_port == 7700

    def test_llm_api_key_from_env(self) -> None:
        """LLM_API_KEY env var should override default."""
        with patch.dict(
            os.environ, {**_CLEAN_ENV, "LLM_API_KEY": "env-api-key"}, clear=True
        ):
            config = TriageAgentConfig()

        assert config.llm_api_key == "env-api-key"

    def test_prometheus_url_from_env(self) -> None:
        """PROMETHEUS_URL env var should override default."""
        with patch.dict(
            os.environ,
            {**_CLEAN_ENV, "PROMETHEUS_URL": "http://custom-prom:9090"},
            clear=True,
        ):
            config = TriageAgentConfig()

        assert config.prometheus_url == "http://custom-prom:9090"

    def test_multiple_overrides(self) -> None:
        """Multiple env vars should all take effect simultaneously."""
        with patch.dict(
            os.environ,
            {
                **_CLEAN_ENV,
                "MEMGRAPH_HOST": "remote-mg",
                "MEMGRAPH_PORT": "7700",
                "LLM_API_KEY": "env-key",
                "PROMETHEUS_URL": "http://prom2:9090",
                "LOKI_URL": "http://loki2:3100",
            },
            clear=True,
        ):
            config = TriageAgentConfig()

        assert config.memgraph_host == "remote-mg"
        assert config.memgraph_port == 7700
        assert config.llm_api_key == "env-key"
        assert config.prometheus_url == "http://prom2:9090"
        assert config.loki_url == "http://loki2:3100"


class TestInvalidPortRaisesValueError:
    """Invalid port raises ValueError with descriptive message."""

    def test_negative_port(self) -> None:
        """Negative port should raise ValueError."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            with pytest.raises(ValueError, match="must be positive"):
                TriageAgentConfig(llm_api_key="test-key", memgraph_port=-1)

    def test_zero_port(self) -> None:
        """Zero port should raise ValueError."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            with pytest.raises(ValueError, match="must be positive"):
                TriageAgentConfig(llm_api_key="test-key", memgraph_port=0)

    def test_valid_port_does_not_raise(self) -> None:
        """Positive port should not raise."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key", memgraph_port=1234)

        assert config.memgraph_port == 1234


class TestInvalidUrlRaisesValueError:
    """Invalid URL raises ValueError with descriptive message."""

    def test_prometheus_url_missing_scheme(self) -> None:
        """prometheus_url without http:// should raise ValueError."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            with pytest.raises(ValueError, match="must start with http"):
                TriageAgentConfig(
                    llm_api_key="test-key",
                    prometheus_url="prometheus:9090",
                )

    def test_loki_url_missing_scheme(self) -> None:
        """loki_url without http:// should raise ValueError."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            with pytest.raises(ValueError, match="must start with http"):
                TriageAgentConfig(
                    llm_api_key="test-key",
                    loki_url="loki:3100",
                )

    def test_https_url_is_valid(self) -> None:
        """https:// URLs should pass validation."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(
                llm_api_key="test-key",
                prometheus_url="https://prom.example.com:9090",
            )

        assert config.prometheus_url == "https://prom.example.com:9090"

    def test_ftp_url_is_invalid(self) -> None:
        """ftp:// URLs should fail validation."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            with pytest.raises(ValueError, match="must start with http"):
                TriageAgentConfig(
                    llm_api_key="test-key",
                    prometheus_url="ftp://prometheus:9090",
                )


class TestMemgraphUriProperty:
    """memgraph_uri property computed correctly from host and port."""

    def test_default_uri(self) -> None:
        """Default host/port should produce bolt://localhost:7687."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(llm_api_key="test-key")

        assert config.memgraph_uri == "bolt://localhost:7687"

    def test_custom_host_and_port(self) -> None:
        """Custom host/port should be reflected in URI."""
        with patch.dict(os.environ, _CLEAN_ENV, clear=True):
            config = TriageAgentConfig(
                llm_api_key="test-key",
                memgraph_host="memgraph-server",
                memgraph_port=7688,
            )

        assert config.memgraph_uri == "bolt://memgraph-server:7688"

    def test_uri_from_env_override(self) -> None:
        """URI should reflect env var overrides."""
        with patch.dict(
            os.environ,
            {**_CLEAN_ENV, "MEMGRAPH_HOST": "mg-prod", "MEMGRAPH_PORT": "17687"},
            clear=True,
        ):
            config = TriageAgentConfig()

        assert config.memgraph_uri == "bolt://mg-prod:17687"


class TestGetConfigSingleton:
    """get_config() returns singleton via lru_cache."""

    def test_returns_triage_agent_config(self) -> None:
        """get_config() should return a TriageAgentConfig instance."""
        get_config.cache_clear()

        with patch.dict(os.environ, {**_CLEAN_ENV, "LLM_API_KEY": "test-key"}, clear=True):
            config = get_config()

        assert isinstance(config, TriageAgentConfig)

    def test_same_instance_on_repeat_calls(self) -> None:
        """Repeated get_config() calls should return the same object (identity)."""
        get_config.cache_clear()

        with patch.dict(os.environ, {**_CLEAN_ENV, "LLM_API_KEY": "test-key"}, clear=True):
            config1 = get_config()
            config2 = get_config()

        assert config1 is config2

    def test_cache_clear_yields_new_instance(self) -> None:
        """After cache_clear(), get_config() should create a fresh instance."""
        get_config.cache_clear()

        with patch.dict(os.environ, {**_CLEAN_ENV, "LLM_API_KEY": "key-1"}, clear=True):
            first = get_config()

        get_config.cache_clear()

        with patch.dict(os.environ, {**_CLEAN_ENV, "LLM_API_KEY": "key-2"}, clear=True):
            second = get_config()

        assert first is not second
        assert first.llm_api_key == "key-1"
        assert second.llm_api_key == "key-2"
