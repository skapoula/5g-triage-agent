"""Tests for RCAAgent - the only agent that uses an LLM."""

import json
from typing import Any
from unittest.mock import patch

import pytest

from triage_agent.agents.rca_agent import (
    RCA_PROMPT_TEMPLATE,
    format_logs_for_prompt,
    format_metrics_for_prompt,
    format_trace_deviations_for_prompt,
    rca_agent_first_attempt,
)
from triage_agent.state import TriageState


class TestFormatMetricsForPrompt:
    """Tests for format_metrics_for_prompt helper."""

    def test_none_returns_no_metrics_message(self) -> None:
        """None metrics should return descriptive string."""
        assert format_metrics_for_prompt(None) == "No metrics available."

    def test_empty_dict_returns_no_metrics_message(self) -> None:
        """Empty dict should return descriptive string."""
        assert format_metrics_for_prompt({}) == "No metrics available."

    def test_formats_as_json(self) -> None:
        """Non-empty metrics should be formatted as indented JSON."""
        metrics = {"AMF": [{"error_rate": 0.05}]}
        result = format_metrics_for_prompt(metrics)
        parsed = json.loads(result)
        assert parsed == metrics


class TestFormatLogsForPrompt:
    """Tests for format_logs_for_prompt helper."""

    def test_none_returns_no_logs_message(self) -> None:
        """None logs should return descriptive string."""
        assert format_logs_for_prompt(None) == "No logs available."

    def test_empty_dict_returns_no_logs_message(self) -> None:
        """Empty dict should return descriptive string."""
        assert format_logs_for_prompt({}) == "No logs available."

    def test_formats_as_json(self) -> None:
        """Non-empty logs should be formatted as indented JSON."""
        logs = {"AMF": [{"message": "error", "level": "ERROR"}]}
        result = format_logs_for_prompt(logs)
        parsed = json.loads(result)
        assert parsed == logs


class TestFormatTraceDeviationsForPrompt:
    """Tests for format_trace_deviations_for_prompt helper."""

    def test_none_returns_no_deviations_message(self) -> None:
        """None deviations should return descriptive string."""
        result = format_trace_deviations_for_prompt(None)
        assert result == "No UE trace deviations available."

    def test_empty_list_returns_no_deviations_message(self) -> None:
        """Empty list should return descriptive string."""
        result = format_trace_deviations_for_prompt([])
        assert result == "No UE trace deviations available."

    def test_formats_as_json(self) -> None:
        """Non-empty deviations should be formatted as indented JSON."""
        deviations = [{"deviation_point": 9, "expected": "Auth"}]
        result = format_trace_deviations_for_prompt(deviations)
        parsed = json.loads(result)
        assert parsed == deviations


class TestRCAPromptTemplate:
    """Tests for RCA_PROMPT_TEMPLATE."""

    def test_template_has_required_placeholders(self) -> None:
        """Template should contain all expected format placeholders."""
        required_placeholders = [
            "{procedure_name}",
            "{infra_score}",
            "{infra_findings_json}",
            "{dag_json}",
            "{time_window}",
            "{metrics_formatted}",
            "{logs_formatted}",
            "{trace_deviations_formatted}",
            "{evidence_quality_score}",
        ]
        for placeholder in required_placeholders:
            assert placeholder in RCA_PROMPT_TEMPLATE, (
                f"Missing placeholder: {placeholder}"
            )

    def test_template_mentions_layer_determination(self) -> None:
        """Template should include infra_score thresholds for layer decision."""
        assert "0.80" in RCA_PROMPT_TEMPLATE
        assert "0.60" in RCA_PROMPT_TEMPLATE
        assert "0.30" in RCA_PROMPT_TEMPLATE

    def test_template_requests_json_output(self) -> None:
        """Template should request JSON output format."""
        assert '"layer"' in RCA_PROMPT_TEMPLATE
        assert '"root_nf"' in RCA_PROMPT_TEMPLATE
        assert '"confidence"' in RCA_PROMPT_TEMPLATE
        assert '"evidence_chain"' in RCA_PROMPT_TEMPLATE

    def test_template_can_be_formatted(
        self, sample_initial_state: TriageState
    ) -> None:
        """Template should be formattable with real state values."""
        prompt = RCA_PROMPT_TEMPLATE.format(
            procedure_name="Registration_General",
            infra_score=0.15,
            infra_findings_json="{}",
            dag_json="{}",
            time_window="2026-02-15T09:55:00Z to 2026-02-15T10:01:00Z",
            metrics_formatted="No metrics available.",
            logs_formatted="No logs available.",
            trace_deviations_formatted="No UE trace deviations available.",
            evidence_quality_score=0.10,
        )
        assert "Registration_General" in prompt
        assert "0.15" in prompt


class TestRcaAgentFirstAttempt:
    """Tests for rca_agent_first_attempt entry point."""

    def test_calls_llm_analyze_evidence(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """rca_agent_first_attempt should call llm_analyze_evidence."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"

        # Mock the LLM to verify it's called
        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.85,
            "evidence_chain": [],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ) as mock_llm, patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "test"},
        ):
            result = rca_agent_first_attempt(state)

            # Verify llm_analyze_evidence was called
            assert mock_llm.called
            assert result["root_nf"] == "AUSF"

    def test_sets_needs_more_evidence_false_when_confident(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Should set needs_more_evidence=False when confidence >= threshold."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.50

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.85,
            "evidence_chain": [{"source": "logs"}],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ):
            result = rca_agent_first_attempt(state)

            # Confidence 0.85 >= 0.70, should NOT need more evidence
            assert result["needs_more_evidence"] is False
            assert result["final_report"] is not None

    def test_sets_needs_more_evidence_true_when_low_confidence(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Should set needs_more_evidence=True when confidence < threshold."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.50

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.40,
            "evidence_chain": [],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ):
            result = rca_agent_first_attempt(state)

            # Confidence 0.40 < 0.70, should need more evidence
            assert result["needs_more_evidence"] is True
            assert result["evidence_gaps"] is not None
            assert len(result["evidence_gaps"]) > 0

    def test_confidence_threshold_adjusts_with_evidence_quality(self) -> None:
        """High evidence quality (>=0.80) should lower confidence threshold to 0.65."""
        # This documents the decision logic in rca_agent_first_attempt:
        # min_confidence = 0.70 by default
        # if evidence_quality_score >= 0.80: min_confidence = 0.65
        import inspect

        source = inspect.getsource(rca_agent_first_attempt)
        assert "0.70" in source
        assert "0.65" in source
        assert "0.80" in source


class TestRCAOutputStructure:
    """Tests for structured RCAOutput Pydantic model."""

    def test_produces_structured_rca_output(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """RCAAgent should produce a structured RCAOutput with all required fields."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.75

        mock_analysis = {
            "layer": "application",
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "failed_phase": "9",
            "confidence": 0.85,
            "evidence_chain": [
                {
                    "timestamp": "2026-02-15T10:00:00Z",
                    "source": "logs",
                    "nf": "AUSF",
                    "type": "log",
                    "content": "Authentication timeout",
                    "significance": "Primary failure indicator",
                }
            ],
            "alternative_hypotheses": [
                {
                    "layer": "infrastructure",
                    "nf": "ausf-pod",
                    "failure_mode": "network_latency",
                    "confidence": 0.30,
                }
            ],
            "reasoning": "Auth timeout logs in AUSF indicate application-layer issue",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "test"},
        ):
            result = rca_agent_first_attempt(state)

            # Verify all required RCAOutput fields are set in state
            assert result["layer"] == "application"
            assert result["root_nf"] == "AUSF"
            assert result["failure_mode"] == "auth_timeout"
            assert result["confidence"] == 0.85
            assert len(result["evidence_chain"]) == 1
            assert result["evidence_chain"][0]["source"] == "logs"


class TestConfidenceThresholdLogic:
    """Tests for confidence threshold decision logic."""

    def test_default_threshold_0_70(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Default confidence threshold should be 0.70."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.50  # Below 0.80

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.72,  # Above default 0.70 threshold
            "evidence_chain": [{"source": "logs"}],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "test"},
        ):
            result = rca_agent_first_attempt(state)

            # Confidence 0.72 >= 0.70, should NOT need more evidence
            assert result["needs_more_evidence"] is False
            assert result["final_report"] is not None

    def test_lowered_threshold_0_65_when_high_evidence_quality(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """When evidence_quality >= 0.80, threshold should be 0.65."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.85  # >= 0.80

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.67,  # Between 0.65 and 0.70
            "evidence_chain": [{"source": "logs"}],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "test"},
        ):
            result = rca_agent_first_attempt(state)

            # Confidence 0.67 >= 0.65 (lowered threshold), should NOT need more evidence
            assert result["needs_more_evidence"] is False
            assert result["final_report"] is not None

    def test_needs_more_evidence_below_default_threshold(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Confidence below 0.70 (default) should set needs_more_evidence=True."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.50  # Below 0.80

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.60,  # Below 0.70 threshold
            "evidence_chain": [],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.identify_evidence_gaps",
            return_value=["Need IMSI traces"],
        ):
            result = rca_agent_first_attempt(state)

            # Confidence 0.60 < 0.70, should need more evidence
            assert result["needs_more_evidence"] is True
            assert result["evidence_gaps"] is not None

    def test_needs_more_evidence_below_lowered_threshold(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Confidence below 0.65 (lowered threshold) should set needs_more_evidence=True."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.85  # >= 0.80

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.62,  # Below 0.65 lowered threshold
            "evidence_chain": [],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.identify_evidence_gaps",
            return_value=["Need more logs"],
        ):
            result = rca_agent_first_attempt(state)

            # Confidence 0.62 < 0.65, should need more evidence
            assert result["needs_more_evidence"] is True
            assert result["evidence_gaps"] is not None


class TestLLMTimeoutHandling:
    """Tests for LLM timeout and degraded mode fallback."""

    def test_llm_timeout_triggers_degraded_mode(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """LLM timeout should trigger degraded mode fallback."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.75
        state["infra_score"] = 0.85
        state["infra_findings"] = {"OOMKilled": True}

        # Mock LLM timeout
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            side_effect=TimeoutError("LLM request timed out"),
        ):
            result = rca_agent_first_attempt(state)

            # Should handle timeout gracefully with degraded mode
            assert result["degraded_mode"] is True
            assert result["degraded_reason"] is not None
            assert "timeout" in result["degraded_reason"].lower()
            # Should still provide analysis via degraded mode
            assert result["root_nf"] is not None
            assert result["failure_mode"] is not None
            assert result["layer"] is not None

    def test_degraded_mode_sets_fallback_analysis(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Degraded mode should use rule-based fallback analysis."""
        # This test documents the expected behavior when LLM times out:
        # - degraded_mode = True
        # - degraded_reason = "LLM timeout after Xs"
        # - Use deterministic rule-based analysis as fallback
        # - Set confidence to lower value (e.g., 0.50)
        # - Still produce root_nf, failure_mode from heuristics
        pass  # Placeholder for future implementation


class TestEvidenceChainCitations:
    """Tests for mandatory citations in evidence chain."""

    def test_evidence_chain_requires_citations(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Each evidence item must have timestamp, source, nf, type, and content."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.75

        mock_analysis = {
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.85,
            "evidence_chain": [
                {
                    "timestamp": "2026-02-15T10:00:00Z",
                    "source": "logs",
                    "nf": "AUSF",
                    "type": "log",
                    "content": "Authentication timeout after 5s",
                    "significance": "Primary failure indicator",
                },
                {
                    "timestamp": "2026-02-15T09:59:58Z",
                    "source": "metrics",
                    "nf": "AUSF",
                    "type": "metric",
                    "content": "http_request_duration_seconds{nf='AUSF'} = 5.2",
                    "significance": "Confirms slow response",
                },
            ],
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "test"},
        ):
            result = rca_agent_first_attempt(state)

            # Verify evidence chain has mandatory fields
            assert len(result["evidence_chain"]) == 2

            for evidence in result["evidence_chain"]:
                assert "timestamp" in evidence
                assert "source" in evidence
                assert evidence["source"] in [
                    "infrastructure",
                    "metrics",
                    "logs",
                    "traces",
                ]
                assert "nf" in evidence
                assert "type" in evidence
                assert "content" in evidence

    def test_evidence_chain_empty_when_low_confidence(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """Low confidence analysis may have sparse evidence chain."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.30

        mock_analysis = {
            "root_nf": "unknown",
            "failure_mode": "undetermined",
            "confidence": 0.25,
            "evidence_chain": [],  # No strong evidence found
            "layer": "application",
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.identify_evidence_gaps",
            return_value=["Need logs", "Need traces"],
        ):
            result = rca_agent_first_attempt(state)

            # Empty evidence chain is valid when confidence is low
            assert result["evidence_chain"] == []
            assert result["needs_more_evidence"] is True


class TestStateUpdates:
    """Tests for state field updates."""

    def test_updates_all_required_state_fields(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """RCAAgent should update layer, root_nf, failure_mode, confidence in state."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.75

        mock_analysis = {
            "layer": "infrastructure",
            "root_nf": "amf-pod",
            "failure_mode": "OOMKilled",
            "confidence": 0.95,
            "evidence_chain": [
                {
                    "timestamp": "2026-02-15T10:00:00Z",
                    "source": "infrastructure",
                    "nf": "amf-pod",
                    "type": "event",
                    "content": "OOMKilled: container exceeded memory limit",
                    "significance": "Root cause",
                }
            ],
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "Infrastructure failure"},
        ):
            result = rca_agent_first_attempt(state)

            # Verify all required fields are set
            assert result["layer"] == "infrastructure"
            assert result["root_nf"] == "amf-pod"
            assert result["failure_mode"] == "OOMKilled"
            assert result["confidence"] == 0.95
            assert len(result["evidence_chain"]) == 1

    def test_defaults_layer_to_application_if_missing(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """If LLM doesn't return layer, default to 'application'."""
        state = sample_initial_state
        state["dag"] = sample_dag
        state["procedure_name"] = "Registration_General"
        state["evidence_quality_score"] = 0.75

        mock_analysis = {
            # layer field missing
            "root_nf": "AUSF",
            "failure_mode": "auth_timeout",
            "confidence": 0.85,
            "evidence_chain": [],
        }

        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=mock_analysis,
        ), patch(
            "triage_agent.agents.rca_agent.generate_final_report",
            return_value={"summary": "test"},
        ):
            result = rca_agent_first_attempt(state)

            # Should default to "application" if layer is missing
            assert result["layer"] == "application"
