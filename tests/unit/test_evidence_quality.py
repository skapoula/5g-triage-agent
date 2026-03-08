"""Tests for evidence quality scoring."""


import pytest

from triage_agent.agents.evidence_quality import compute_evidence_quality
from triage_agent.state import TriageState


class TestComputeEvidenceQuality:
    """Tests for evidence quality computation."""

    def test_all_three_sources_high_quality(
        self, sample_initial_state: TriageState
    ) -> None:
        """Metrics + logs + traces should give 0.95 quality."""
        state = sample_initial_state
        state["metrics"] = {"AMF": [{"error_rate": 0.1}]}
        state["logs"] = {"AMF": [{"message": "error"}]}
        state["traces_ready"] = True

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.95, abs=0.01)

    def test_traces_plus_metrics(self, sample_initial_state: TriageState) -> None:
        """Traces + metrics should give 0.85 quality."""
        state = sample_initial_state
        state["metrics"] = {"AMF": [{"error_rate": 0.1}]}
        state["logs"] = None
        state["traces_ready"] = True

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.85, abs=0.01)

    def test_traces_plus_logs(self, sample_initial_state: TriageState) -> None:
        """Traces + logs should give 0.85 quality."""
        state = sample_initial_state
        state["metrics"] = None
        state["logs"] = {"AMF": [{"message": "error"}]}
        state["traces_ready"] = True

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.85, abs=0.01)

    def test_metrics_plus_logs_no_traces(
        self, sample_initial_state: TriageState
    ) -> None:
        """Metrics + logs (no traces) should give 0.80 quality."""
        state = sample_initial_state
        state["metrics"] = {"AMF": [{"error_rate": 0.1}]}
        state["logs"] = {"AMF": [{"message": "error"}]}
        state["traces_ready"] = False

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.80, abs=0.01)

    def test_traces_only(self, sample_initial_state: TriageState) -> None:
        """Traces only should give 0.50 quality."""
        state = sample_initial_state
        state["metrics"] = None
        state["logs"] = None
        state["traces_ready"] = True

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.50, abs=0.01)

    def test_metrics_only(self, sample_initial_state: TriageState) -> None:
        """Metrics only should give 0.40 quality."""
        state = sample_initial_state
        state["metrics"] = {"AMF": [{"error_rate": 0.1}]}
        state["logs"] = None
        state["traces_ready"] = False

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.40, abs=0.01)

    def test_logs_only(self, sample_initial_state: TriageState) -> None:
        """Logs only should give 0.35 quality."""
        state = sample_initial_state
        state["metrics"] = None
        state["logs"] = {"AMF": [{"message": "error"}]}
        state["traces_ready"] = False

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.35, abs=0.01)

    def test_no_evidence(self, sample_initial_state: TriageState) -> None:
        """No evidence should give 0.10 quality."""
        state = sample_initial_state
        state["metrics"] = None
        state["logs"] = None
        state["traces_ready"] = False

        result = compute_evidence_quality(state)

        assert result["evidence_quality_score"] == pytest.approx(0.10, abs=0.01)

    def test_empty_dicts_treated_as_no_data(
        self, sample_initial_state: TriageState
    ) -> None:
        """Empty dicts should be treated as no data."""
        state = sample_initial_state
        state["metrics"] = {}
        state["logs"] = {}
        state["traces_ready"] = False

        result = compute_evidence_quality(state)

        # Empty dict is falsy, so treated as no data
        assert result["evidence_quality_score"] == pytest.approx(0.10, abs=0.01)

    def test_returns_only_delta_dict(
        self, sample_initial_state: TriageState
    ) -> None:
        """compute_evidence_quality returns only {'evidence_quality_score': float}."""
        state = sample_initial_state
        state["metrics"] = {"AMF": []}
        state["logs"] = None
        state["traces_ready"] = False

        result = compute_evidence_quality(state)

        assert set(result.keys()) == {"evidence_quality_score"}


def test_saves_artifact(sample_initial_state: TriageState) -> None:
    """compute_evidence_quality saves an artifact with score and source types."""
    from unittest.mock import patch

    state = sample_initial_state
    state["incident_id"] = "test-inc-001"
    state["metrics"] = {"AMF": [{"error_rate": 0.1}]}
    state["logs"] = {"AMF": [{"message": "error"}]}
    state["traces_ready"] = True

    with patch("triage_agent.agents.evidence_quality.save_artifact") as mock_save:
        result = compute_evidence_quality(state)

    assert result["evidence_quality_score"] == pytest.approx(0.95, abs=0.01)
    mock_save.assert_called_once()
    _incident_id, _name, artifact_data, _artifacts_dir = mock_save.call_args.args
    assert _incident_id == "test-inc-001"
    assert _name == "evidence_quality.json"
    assert artifact_data["score"] == pytest.approx(0.95, abs=0.01)
    assert artifact_data["metrics_present"] is True
    assert artifact_data["logs_present"] is True
    assert artifact_data["traces_ready"] is True
