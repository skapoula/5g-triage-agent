"""Tests for TriageState TypedDict."""

from typing import get_type_hints

from triage_agent.state import TriageState


class TestTriageStateFields:
    """Tests for TriageState field definitions."""

    def test_has_all_required_input_fields(self) -> None:
        """TriageState must define the alert input field."""
        hints = get_type_hints(TriageState)
        assert "alert" in hints

    def test_has_infra_agent_fields(self) -> None:
        """TriageState must define InfraAgent output fields."""
        hints = get_type_hints(TriageState)
        assert "infra_checked" in hints
        assert "infra_score" in hints
        assert "infra_findings" in hints

    def test_has_dag_mapping_fields(self) -> None:
        """TriageState must define DAG mapping fields."""
        hints = get_type_hints(TriageState)
        assert "procedure_name" in hints
        assert "dag_id" in hints
        assert "dag" in hints
        assert "mapping_confidence" in hints
        assert "mapping_method" in hints

    def test_has_data_collection_fields(self) -> None:
        """TriageState must define NfMetrics/NfLogs/UeTraces fields."""
        hints = get_type_hints(TriageState)
        assert "metrics" in hints
        assert "logs" in hints
        assert "discovered_imsis" in hints
        assert "traces_ready" in hints
        assert "trace_deviations" in hints
        assert "incident_id" in hints
        assert "evidence_quality_score" in hints

    def test_has_rca_agent_fields(self) -> None:
        """TriageState must define RCAAgent output fields."""
        hints = get_type_hints(TriageState)
        assert "root_nf" in hints
        assert "failure_mode" in hints
        assert "layer" in hints
        assert "confidence" in hints
        assert "evidence_chain" in hints
        assert "degraded_mode" in hints
        assert "degraded_reason" in hints

    def test_has_control_flow_fields(self) -> None:
        """TriageState must define control flow fields."""
        hints = get_type_hints(TriageState)
        assert "attempt_count" in hints
        assert "max_attempts" in hints
        assert "needs_more_evidence" in hints
        assert "second_attempt_complete" in hints
        assert "final_report" in hints

    def test_can_instantiate_with_all_fields(self) -> None:
        """TriageState should be instantiable with all fields."""
        state = TriageState(
            alert={"status": "firing", "labels": {}, "startsAt": "2026-01-01T00:00:00Z"},
            incident_id="test-001",
            infra_checked=False,
            infra_score=0.0,
            infra_findings=None,
            procedure_name=None,
            dag_id=None,
            dag=None,
            mapping_confidence=0.0,
            mapping_method="",
            metrics=None,
            logs=None,
            discovered_imsis=None,
            traces_ready=False,
            trace_deviations=None,
            evidence_quality_score=0.0,
            root_nf=None,
            failure_mode=None,
            layer="",
            confidence=0.0,
            evidence_chain=[],
            degraded_mode=False,
            degraded_reason=None,
            attempt_count=1,
            max_attempts=2,
            needs_more_evidence=False,
            second_attempt_complete=False,
            final_report=None,
        )
        assert state["incident_id"] == "test-001"
        assert state["infra_score"] == 0.0
        assert state["evidence_chain"] == []

    def test_state_is_mutable_dict(self) -> None:
        """TriageState should behave as a mutable dict (agents update it)."""
        state = TriageState(
            alert={},
            incident_id="test-002",
            infra_checked=False,
            infra_score=0.0,
            infra_findings=None,
            procedure_name=None,
            dag_id=None,
            dag=None,
            mapping_confidence=0.0,
            mapping_method="",
            metrics=None,
            logs=None,
            discovered_imsis=None,
            traces_ready=False,
            trace_deviations=None,
            evidence_quality_score=0.0,
            root_nf=None,
            failure_mode=None,
            layer="",
            confidence=0.0,
            evidence_chain=[],
            degraded_mode=False,
            degraded_reason=None,
            attempt_count=1,
            max_attempts=2,
            needs_more_evidence=False,
            second_attempt_complete=False,
            final_report=None,
        )
        state["infra_checked"] = True
        state["infra_score"] = 0.85
        assert state["infra_checked"] is True
        assert state["infra_score"] == 0.85
