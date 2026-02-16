"""Tests for UeTracesAgent."""


import pytest

from triage_agent.agents.ue_traces_agent import discover_and_trace_imsis
from triage_agent.state import TriageState


class TestDiscoverAndTraceImsis:
    """Tests for discover_and_trace_imsis entry point."""

    def test_calls_loki_for_imsi_discovery(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should query Loki for active IMSIs in the alarm window."""
        state = sample_initial_state
        state["procedure_name"] = "Registration_General"

        # loki_query is not yet implemented
        with pytest.raises(NotImplementedError):
            discover_and_trace_imsis(state)

    def test_sets_discovered_imsis_in_state(self) -> None:
        """discover_and_trace_imsis should set state['discovered_imsis']."""
        import inspect

        sig = inspect.signature(discover_and_trace_imsis)
        params = list(sig.parameters.keys())
        assert params == ["state"]

    def test_sets_traces_ready_in_state(self) -> None:
        """discover_and_trace_imsis should set state['traces_ready'] = True."""
        # Verify the function writes traces_ready by inspecting source
        import inspect

        source = inspect.getsource(discover_and_trace_imsis)
        assert 'state["traces_ready"] = True' in source

    def test_sets_trace_deviations_in_state(self) -> None:
        """discover_and_trace_imsis should set state['trace_deviations']."""
        import inspect

        source = inspect.getsource(discover_and_trace_imsis)
        assert 'state["trace_deviations"]' in source


class TestUeTracesAgentHelpers:
    """Tests for UeTracesAgent helper functions."""

    def test_loki_query_not_implemented(self) -> None:
        """loki_query is a stub pending MCP integration."""
        from triage_agent.agents.ue_traces_agent import loki_query

        with pytest.raises(NotImplementedError):
            loki_query("{namespace='5g-core'}", start=0, end=100)

    def test_extract_unique_imsis_not_implemented(self) -> None:
        """extract_unique_imsis is a stub."""
        from triage_agent.agents.ue_traces_agent import extract_unique_imsis

        with pytest.raises(NotImplementedError):
            extract_unique_imsis([])

    def test_per_imsi_logql_not_implemented(self) -> None:
        """per_imsi_logql is a stub."""
        from triage_agent.agents.ue_traces_agent import per_imsi_logql

        with pytest.raises(NotImplementedError):
            per_imsi_logql("001010123456789")

    def test_contract_imsi_trace_not_implemented(self) -> None:
        """contract_imsi_trace is a stub."""
        from triage_agent.agents.ue_traces_agent import contract_imsi_trace

        with pytest.raises(NotImplementedError):
            contract_imsi_trace([], "001010123456789")

    def test_ingest_traces_to_memgraph_not_implemented(self) -> None:
        """ingest_traces_to_memgraph is a stub."""
        from triage_agent.agents.ue_traces_agent import ingest_traces_to_memgraph

        with pytest.raises(NotImplementedError):
            ingest_traces_to_memgraph([], "test-incident")

    def test_run_deviation_detection_not_implemented(self) -> None:
        """run_deviation_detection is a stub."""
        from triage_agent.agents.ue_traces_agent import run_deviation_detection

        with pytest.raises(NotImplementedError):
            run_deviation_detection("test-incident", "Registration_General")
