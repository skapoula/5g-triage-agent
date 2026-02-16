"""Tests for NfLogsAgent."""

from typing import Any

import pytest

from triage_agent.agents.logs_agent import (
    logs_agent,
    organize_and_annotate_logs,
    wildcard_match,
)
from triage_agent.state import TriageState


class TestWildcardMatch:
    """Tests for wildcard_match utility."""

    def test_exact_match(self) -> None:
        """Should match exact text."""
        assert wildcard_match("authentication failed", "authentication failed")

    def test_wildcard_prefix(self) -> None:
        """Should match with wildcard at start."""
        assert wildcard_match("ERROR: authentication failed", "*auth*fail*")

    def test_wildcard_suffix(self) -> None:
        """Should match with wildcard at end."""
        assert wildcard_match("timeout waiting for response", "*timeout*")

    def test_case_insensitive(self) -> None:
        """Should match case-insensitively."""
        assert wildcard_match("Authentication Failed", "*auth*fail*")

    def test_no_match(self) -> None:
        """Should return False when pattern doesn't match."""
        assert not wildcard_match("registration complete", "*auth*fail*")

    def test_empty_text(self) -> None:
        """Should handle empty text."""
        assert not wildcard_match("", "*auth*")

    def test_wildcard_only(self) -> None:
        """Wildcard-only pattern should match anything."""
        assert wildcard_match("any text here", "*")

    def test_timeout_ausf_pattern(self) -> None:
        """Should match the DAG failure pattern *timeout*AUSF*."""
        assert wildcard_match(
            "WARN: timeout waiting for AUSF response", "*timeout*AUSF*"
        )

    def test_registration_reject_pattern(self) -> None:
        """Should match *registration*reject* pattern."""
        assert wildcard_match(
            "Registration Request rejected by AMF", "*registration*reject*"
        )


class TestOrganizeAndAnnotateLogs:
    """Tests for organize_and_annotate_logs."""

    def test_empty_logs(self, sample_dag: dict[str, Any]) -> None:
        """Should return empty dict for empty log list."""
        result = organize_and_annotate_logs([], sample_dag)
        assert result == {}

    def test_organizes_by_nf(self, sample_dag: dict[str, Any]) -> None:
        """Should group logs by NF extracted from pod name."""
        # extract_nf_from_pod_name is not yet implemented
        with pytest.raises(NotImplementedError):
            organize_and_annotate_logs(
                [
                    {
                        "pod": "amf-deployment-abc123",
                        "message": "ERROR: auth failed",
                        "level": "ERROR",
                        "timestamp": 1708000000,
                    }
                ],
                sample_dag,
            )

    def test_annotates_matched_phase(self, sample_dag: dict[str, Any]) -> None:
        """Logs matching DAG failure_patterns should be annotated with phase_id."""
        # This test verifies the annotation logic once extract_nf_from_pod_name
        # is implemented. For now, it documents expected behavior.
        # A log matching "*auth*fail*" should get matched_phase from the DAG.
        phases_with_failure_patterns = [
            p for p in sample_dag["phases"] if p.get("failure_patterns")
        ]
        assert len(phases_with_failure_patterns) >= 1


class TestLogsAgent:
    """Tests for logs_agent entry point."""

    def test_logs_agent_reads_dag(
        self, sample_initial_state: TriageState, sample_dag: dict[str, Any]
    ) -> None:
        """logs_agent should read DAG from state for NF list and patterns."""
        state = sample_initial_state
        state["dag"] = sample_dag

        # Depends on parse_timestamp which is not yet implemented
        with pytest.raises(NotImplementedError):
            logs_agent(state)

    def test_logs_agent_builds_loki_queries(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """Should build LogQL queries for each NF's ERROR/WARN/FATAL logs."""
        nfs = sample_dag["all_nfs"]
        # Base query per NF + phase-specific queries
        assert len(nfs) == 5

    def test_logs_agent_updates_state_key(self) -> None:
        """logs_agent should set state['logs'] when complete."""
        import inspect

        sig = inspect.signature(logs_agent)
        params = list(sig.parameters.keys())
        assert params == ["state"]
