"""Tests for LangGraph workflow definition."""

from typing import Any

from triage_agent.graph import (
    finalize_report,
    get_initial_state,
    increment_attempt,
    should_retry,
)
from triage_agent.state import TriageState


class TestShouldRetry:
    """Tests for the should_retry conditional edge."""

    def test_retry_when_needs_more_evidence_and_under_max(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should return 'retry' when needs_more_evidence and under max_attempts."""
        state = sample_initial_state
        state["needs_more_evidence"] = True
        state["attempt_count"] = 1
        state["max_attempts"] = 2

        assert should_retry(state) == "retry"

    def test_finalize_when_confident(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should return 'finalize' when not needing more evidence."""
        state = sample_initial_state
        state["needs_more_evidence"] = False
        state["attempt_count"] = 1

        assert should_retry(state) == "finalize"

    def test_finalize_when_max_attempts_reached(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should return 'finalize' when max attempts reached."""
        state = sample_initial_state
        state["needs_more_evidence"] = True
        state["attempt_count"] = 2
        state["max_attempts"] = 2

        assert should_retry(state) == "finalize"

    def test_finalize_when_over_max_attempts(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should return 'finalize' when attempt_count exceeds max_attempts."""
        state = sample_initial_state
        state["needs_more_evidence"] = True
        state["attempt_count"] = 3
        state["max_attempts"] = 2

        assert should_retry(state) == "finalize"

    def test_defaults_to_finalize(self) -> None:
        """Should finalize by default when state keys are missing."""
        state: dict[str, Any] = {}
        assert should_retry(state) == "finalize"  # type: ignore[arg-type]


class TestIncrementAttempt:
    """Tests for the increment_attempt node."""

    def test_increments_from_one_to_two(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should increment attempt_count from 1 to 2."""
        state = sample_initial_state
        state["attempt_count"] = 1

        result = increment_attempt(state)

        assert result["attempt_count"] == 2

    def test_increments_from_two_to_three(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should increment attempt_count from 2 to 3."""
        state = sample_initial_state
        state["attempt_count"] = 2

        result = increment_attempt(state)

        assert result["attempt_count"] == 3

    def test_defaults_to_one_when_missing(self) -> None:
        """Should default attempt_count to 1 then increment to 2."""
        state: dict[str, Any] = {}
        result = increment_attempt(state)  # type: ignore[arg-type]
        assert result["attempt_count"] == 2


class TestFinalizeReport:
    """Tests for the finalize_report node."""

    def test_creates_final_report(
        self, sample_initial_state: TriageState
    ) -> None:
        """Should populate final_report with key fields."""
        state = sample_initial_state
        state["incident_id"] = "inc-123"
        state["layer"] = "application"
        state["root_nf"] = "AUSF"
        state["failure_mode"] = "auth_timeout"
        state["confidence"] = 0.85
        state["evidence_chain"] = [{"source": "logs"}]
        state["infra_score"] = 0.1
        state["evidence_quality_score"] = 0.95
        state["attempt_count"] = 1

        result = finalize_report(state)

        report = result["final_report"]
        assert report is not None
        assert report["incident_id"] == "inc-123"
        assert report["layer"] == "application"
        assert report["root_nf"] == "AUSF"
        assert report["failure_mode"] == "auth_timeout"
        assert report["confidence"] == 0.85
        assert report["evidence_chain"] == [{"source": "logs"}]
        assert report["infra_score"] == 0.1
        assert report["evidence_quality_score"] == 0.95
        assert report["attempt_count"] == 1

    def test_final_report_includes_degraded_mode(
        self, sample_initial_state: TriageState
    ) -> None:
        """Final report should include degraded_mode flag."""
        state = sample_initial_state
        state["degraded_mode"] = True

        result = finalize_report(state)

        assert result["final_report"]["degraded_mode"] is True

    def test_final_report_defaults(
        self, sample_initial_state: TriageState
    ) -> None:
        """Final report should handle missing/default values."""
        result = finalize_report(sample_initial_state)

        report = result["final_report"]
        assert report["incident_id"] == "test-incident-001"
        assert report["evidence_chain"] == []
        assert report["degraded_mode"] is False
        assert report["attempt_count"] == 1


class TestGetInitialState:
    """Tests for get_initial_state factory."""

    def test_creates_state_from_alert(self, sample_alert: dict[str, Any]) -> None:
        """Should create TriageState from alert payload."""
        state = get_initial_state(alert=sample_alert, incident_id="inc-001")

        assert state["alert"] == sample_alert
        assert state["incident_id"] == "inc-001"

    def test_initial_state_defaults(self, sample_alert: dict[str, Any]) -> None:
        """Initial state should have sensible defaults."""
        state = get_initial_state(alert=sample_alert, incident_id="inc-002")

        assert state["infra_checked"] is False
        assert state["infra_score"] == 0.0
        assert state["infra_findings"] is None
        assert state["metrics"] is None
        assert state["logs"] is None
        assert state["traces_ready"] is False
        assert state["confidence"] == 0.0
        assert state["attempt_count"] == 1
        assert state["max_attempts"] == 2
        assert state["needs_more_evidence"] is False
        assert state["final_report"] is None
        assert state["degraded_mode"] is False
        assert state["evidence_chain"] == []

    def test_initial_state_has_empty_evidence(
        self, sample_alert: dict[str, Any]
    ) -> None:
        """Initial state should start with no evidence collected."""
        state = get_initial_state(alert=sample_alert, incident_id="inc-003")

        assert state["discovered_imsis"] is None
        assert state["trace_deviations"] is None
        assert state["root_nf"] is None
        assert state["failure_mode"] is None


class TestCreateWorkflow:
    """Tests for create_workflow graph structure."""

    def test_workflow_compiles(self) -> None:
        """create_workflow should compile without errors."""
        from triage_agent.graph import create_workflow

        workflow = create_workflow()
        assert workflow is not None

    def test_workflow_has_expected_nodes(self) -> None:
        """Workflow graph should contain all expected agent nodes."""
        from triage_agent.graph import create_workflow

        workflow = create_workflow()
        graph = workflow.get_graph()
        node_ids = set(graph.nodes.keys())

        expected_nodes = {
            "infra_agent",
            "metrics_agent",
            "logs_agent",
            "traces_agent",
            "evidence_quality",
            "rca_agent",
            "increment_attempt",
            "finalize",
        }
        assert expected_nodes.issubset(node_ids)
