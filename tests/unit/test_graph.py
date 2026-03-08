"""Tests for LangGraph workflow definition."""


import pytest

from triage_agent.graph import create_workflow, finalize_report, should_retry
from triage_agent.state import TriageState


class TestShouldRetry:
    """Tests for the should_retry conditional edge function."""

    def test_returns_retry_when_needs_more_evidence_and_below_max(
        self, sample_initial_state: TriageState
    ) -> None:
        """Returns 'retry' when needs_more_evidence=True and attempt < max_attempts."""
        state = sample_initial_state
        state["needs_more_evidence"] = True
        state["attempt_count"] = 1
        state["max_attempts"] = 2

        assert should_retry(state) == "retry"

    def test_returns_finalize_when_needs_more_evidence_false(
        self, sample_initial_state: TriageState
    ) -> None:
        """Returns 'finalize' when no more evidence is needed regardless of attempt."""
        state = sample_initial_state
        state["needs_more_evidence"] = False
        state["attempt_count"] = 1
        state["max_attempts"] = 2

        assert should_retry(state) == "finalize"

    def test_returns_finalize_at_max_attempts(
        self, sample_initial_state: TriageState
    ) -> None:
        """Returns 'finalize' when attempt_count equals max_attempts (boundary)."""
        state = sample_initial_state
        state["needs_more_evidence"] = True
        state["attempt_count"] = 2
        state["max_attempts"] = 2

        assert should_retry(state) == "finalize"

    def test_returns_finalize_when_beyond_max_attempts(
        self, sample_initial_state: TriageState
    ) -> None:
        """Returns 'finalize' when attempt_count exceeds max_attempts."""
        state = sample_initial_state
        state["needs_more_evidence"] = True
        state["attempt_count"] = 3
        state["max_attempts"] = 2

        assert should_retry(state) == "finalize"


class TestFinalizeReport:
    """Tests for the finalize_report node function."""

    def test_creates_final_report_dict(
        self, sample_initial_state: TriageState
    ) -> None:
        """finalize_report creates a final_report dict with all required keys."""
        state = sample_initial_state
        required_keys = {
            "incident_id",
            "layer",
            "root_nf",
            "failure_mode",
            "confidence",
            "evidence_chain",
            "infra_score",
            "evidence_quality_score",
            "attempt_count",
            "procedure_names",
            "mapping_confidence",
            "mapping_method",
            "nf_union",
        }

        result = finalize_report(state)

        assert "final_report" in result
        assert required_keys.issubset(result["final_report"].keys())

    def test_final_report_values_from_state(
        self, sample_initial_state: TriageState
    ) -> None:
        """final_report values are sourced from state fields."""
        state = sample_initial_state
        state["root_nf"] = "AMF"
        state["failure_mode"] = "auth_failure"
        state["layer"] = "application"
        state["confidence"] = 0.85
        state["attempt_count"] = 1

        result = finalize_report(state)
        report = result["final_report"]

        assert report["root_nf"] == "AMF"
        assert report["failure_mode"] == "auth_failure"
        assert report["layer"] == "application"
        assert report["confidence"] == pytest.approx(0.85)
        assert report["attempt_count"] == 1

    def test_final_report_evidence_chain_defaults_to_empty_list(
        self, sample_initial_state: TriageState
    ) -> None:
        """evidence_chain defaults to [] when not set in state."""
        state = sample_initial_state
        state["evidence_chain"] = []

        result = finalize_report(state)

        assert result["final_report"]["evidence_chain"] == []


class TestCreateWorkflow:
    """Tests for the create_workflow LangGraph DAG builder."""

    def test_workflow_compiles_without_error(self) -> None:
        """create_workflow() compiles successfully and returns a compiled graph."""
        workflow = create_workflow()

        assert workflow is not None

    def test_parallel_edges_from_start(self) -> None:
        """Both infra_agent and dag_mapper have edges from START (parallel execution)."""
        graph = create_workflow().get_graph()
        edge_pairs = [(e.source, e.target) for e in graph.edges]

        assert ("__start__", "infra_agent") in edge_pairs
        assert ("__start__", "dag_mapper") in edge_pairs

    def test_conditional_edge_from_rca_agent(self) -> None:
        """rca_agent has conditional edges to both increment_attempt and finalize."""
        graph = create_workflow().get_graph()
        rca_targets = {e.target for e in graph.edges if e.source == "rca_agent"}

        assert "increment_attempt" in rca_targets
        assert "finalize" in rca_targets

    def test_dag_mapper_fans_out_to_all_three_agents(self) -> None:
        """dag_mapper has edges to metrics_agent, logs_agent, and traces_agent."""
        graph = create_workflow().get_graph()
        dag_mapper_targets = {e.target for e in graph.edges if e.source == "dag_mapper"}

        assert "metrics_agent" in dag_mapper_targets
        assert "logs_agent" in dag_mapper_targets
        assert "traces_agent" in dag_mapper_targets

    def test_dag_mapper_starts_from_start(self) -> None:
        """dag_mapper has an edge from __start__."""
        graph = create_workflow().get_graph()
        edge_pairs = [(e.source, e.target) for e in graph.edges]

        assert ("__start__", "dag_mapper") in edge_pairs

    def test_all_three_agents_converge_at_evidence_quality(self) -> None:
        """metrics_agent, logs_agent, and traces_agent all have edges to evidence_quality."""
        graph = create_workflow().get_graph()
        edge_pairs = [(e.source, e.target) for e in graph.edges]

        assert ("metrics_agent", "evidence_quality") in edge_pairs
        assert ("logs_agent", "evidence_quality") in edge_pairs
        assert ("traces_agent", "evidence_quality") in edge_pairs

    def test_no_sequential_edges_between_collection_agents(self) -> None:
        """There must be no sequential edges: metrics→logs, logs→traces."""
        graph = create_workflow().get_graph()
        edge_pairs = [(e.source, e.target) for e in graph.edges]

        assert ("metrics_agent", "logs_agent") not in edge_pairs
        assert ("logs_agent", "traces_agent") not in edge_pairs

    def test_join_for_rca_is_barrier_node(self) -> None:
        """join_for_rca is in the graph and has edges from both infra_agent and evidence_quality."""
        graph = create_workflow().get_graph()
        node_names = list(graph.nodes)
        assert "join_for_rca" in node_names

        edge_pairs = [(e.source, e.target) for e in graph.edges]
        assert ("infra_agent", "join_for_rca") in edge_pairs
        assert ("evidence_quality", "join_for_rca") in edge_pairs
        assert ("join_for_rca", "rca_agent") in edge_pairs

    def test_rca_agent_entry_is_join_for_rca(self) -> None:
        """rca_agent's only pipeline entry point is join_for_rca (not evidence_quality directly)."""
        graph = create_workflow().get_graph()
        rca_incoming = {e.source for e in graph.edges if e.target == "rca_agent"}
        pipeline_sources = rca_incoming - {"increment_attempt"}
        assert pipeline_sources == {"join_for_rca"}, (
            f"rca_agent pipeline sources: {pipeline_sources}, expected exactly {{'join_for_rca'}}"
        )
