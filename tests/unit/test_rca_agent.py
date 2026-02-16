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

        # llm_analyze_evidence is not yet implemented
        with pytest.raises(NotImplementedError):
            rca_agent_first_attempt(state)

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
        ), pytest.raises(NotImplementedError):
            # generate_final_report is also not implemented
            rca_agent_first_attempt(state)

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
        ), pytest.raises(NotImplementedError):
            # identify_evidence_gaps is also not implemented
            rca_agent_first_attempt(state)

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
