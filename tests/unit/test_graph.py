"""Tests for LangGraph workflow definition."""

from typing import Any

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
            "degraded_mode",
            "attempt_count",
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

    def test_parallel_edges_for_infra_agent_and_metrics_agent(self) -> None:
        """Both infra_agent and metrics_agent have edges from START (parallel execution)."""
        graph = create_workflow().get_graph()
        edge_pairs = [(e.source, e.target) for e in graph.edges]

        assert ("__start__", "infra_agent") in edge_pairs
        assert ("__start__", "metrics_agent") in edge_pairs

    def test_conditional_edge_from_rca_agent(self) -> None:
        """rca_agent has conditional edges to both increment_attempt and finalize."""
        graph = create_workflow().get_graph()
        rca_targets = {e.target for e in graph.edges if e.source == "rca_agent"}

        assert "increment_attempt" in rca_targets
        assert "finalize" in rca_targets
