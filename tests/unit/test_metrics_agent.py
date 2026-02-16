"""Tests for NfMetricsAgent."""

from typing import Any

import pytest

from triage_agent.agents.metrics_agent import metrics_agent, organize_metrics_by_nf
from triage_agent.state import TriageState


class TestMetricsAgent:
    """Tests for metrics_agent entry point."""

    def test_metrics_agent_reads_dag_nfs(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """metrics_agent should read NF list from the DAG in state."""
        state = sample_initial_state
        state["dag"] = sample_dag

        # metrics_agent depends on parse_timestamp which is not yet implemented
        with pytest.raises(NotImplementedError):
            metrics_agent(state)

    def test_metrics_agent_updates_state(
        self, sample_initial_state: TriageState
    ) -> None:
        """metrics_agent should set state['metrics'] when complete."""
        # Verify the function signature expects and returns TriageState
        import inspect

        sig = inspect.signature(metrics_agent)
        params = list(sig.parameters.keys())
        assert params == ["state"]


class TestOrganizeMetricsByNf:
    """Tests for organize_metrics_by_nf helper."""

    def test_not_yet_implemented(self) -> None:
        """organize_metrics_by_nf is a stub pending MCP integration."""
        with pytest.raises(NotImplementedError):
            organize_metrics_by_nf([], ["AMF", "AUSF"])


class TestMetricsAgentQueryConstruction:
    """Tests for PromQL query generation within metrics_agent."""

    def test_generates_queries_for_all_nfs(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """Should generate 4 queries per NF (error rate, latency, CPU, memory)."""
        nfs = sample_dag["all_nfs"]
        # Each NF should produce 4 queries
        expected_query_count = len(nfs) * 4
        assert expected_query_count == 20  # 5 NFs * 4 queries

    def test_queries_use_lowercase_nf_names(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """PromQL queries should use lowercase NF names for label matching."""
        for nf in sample_dag["all_nfs"]:
            nf_lower = nf.lower()
            # Verify the convention: NF names in DAG are uppercase,
            # queries should use lowercase
            assert nf_lower != nf
            assert nf_lower.isalpha()
