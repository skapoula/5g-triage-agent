"""Tests for InfraAgent - write tests first, then implement.

This test file follows the test-first development approach.
Tests are written based on the specification before implementation.
"""

import pytest

from triage_agent.agents.infra_agent import (
    INFRA_PROMETHEUS_QUERIES,
    compute_infrastructure_score,
)


class TestComputeInfrastructureScore:
    """Tests for infrastructure score computation.
    
    Weight table:
    | Factor | Weight | Scoring |
    | Restarts | 0.35 | 0:0.0, 1-2:0.4, 3-5:0.7, >5:1.0 |
    | OOM | 0.25 | 0:0.0, >0:1.0 |
    | Pod Status | 0.20 | Running:0.0, Pending:0.6, Failed:1.0 |
    | Resources | 0.20 | Mem>90%:1.0, CPU>1.0:0.8, Normal:0.0 |
    """

    def test_all_healthy_metrics_returns_near_zero(self) -> None:
        """All healthy metrics should result in score near 0.0."""
        metrics = {
            "pod_restarts": [{"pod": "amf-1", "value": 0}],
            "oom_kills": [],
            "cpu_usage": [{"pod": "amf-1", "value": 0.3}],
            "memory_percent": [{"pod": "amf-1", "value": 50}],
            "pod_status": [{"pod": "amf-1", "phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.0, abs=0.01)

    def test_pod_restarts_weight(self) -> None:
        """Pod restarts should contribute 0.35 weight to score."""
        # 1-2 restarts = 0.4 factor, weight 0.35 → 0.14 contribution
        metrics = {
            "pod_restarts": [{"pod": "amf-1", "value": 2}],
            "oom_kills": [],
            "cpu_usage": [],
            "memory_percent": [],
            "pod_status": [{"phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.35 * 0.4, abs=0.01)  # 0.14

    def test_pod_restarts_high_count(self) -> None:
        """More than 5 restarts should max out restart factor."""
        metrics = {
            "pod_restarts": [{"pod": "amf-1", "value": 10}],
            "oom_kills": [],
            "cpu_usage": [],
            "memory_percent": [],
            "pod_status": [{"phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.35 * 1.0, abs=0.01)  # 0.35

    def test_oom_kill_critical_weight(self) -> None:
        """OOM kill should contribute 0.25 weight as critical failure."""
        metrics = {
            "pod_restarts": [{"value": 0}],
            "oom_kills": [{"pod": "amf-1", "value": 1}],
            "cpu_usage": [],
            "memory_percent": [],
            "pod_status": [{"phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.25 * 1.0, abs=0.01)  # 0.25

    def test_pod_status_failed(self) -> None:
        """Failed pod status should contribute 0.20 weight."""
        metrics = {
            "pod_restarts": [{"value": 0}],
            "oom_kills": [],
            "cpu_usage": [],
            "memory_percent": [],
            "pod_status": [{"phase": "Failed"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.20 * 1.0, abs=0.01)  # 0.20

    def test_pod_status_pending(self) -> None:
        """Pending pod status should contribute 0.20 * 0.6 weight."""
        metrics = {
            "pod_restarts": [{"value": 0}],
            "oom_kills": [],
            "cpu_usage": [],
            "memory_percent": [],
            "pod_status": [{"phase": "Pending"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.20 * 0.6, abs=0.01)  # 0.12

    def test_memory_saturation(self) -> None:
        """Memory >90% should contribute 0.20 weight."""
        metrics = {
            "pod_restarts": [{"value": 0}],
            "oom_kills": [],
            "cpu_usage": [{"value": 0.5}],
            "memory_percent": [{"value": 95}],
            "pod_status": [{"phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.20 * 1.0, abs=0.01)  # 0.20

    def test_cpu_saturation(self) -> None:
        """CPU >1.0 core should contribute 0.20 * 0.8 weight."""
        metrics = {
            "pod_restarts": [{"value": 0}],
            "oom_kills": [],
            "cpu_usage": [{"value": 1.5}],
            "memory_percent": [{"value": 50}],
            "pod_status": [{"phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.20 * 0.8, abs=0.01)  # 0.16

    def test_combined_issues_accumulate(self) -> None:
        """Multiple issues should accumulate weighted scores."""
        # OOM (0.25) + Memory saturation (0.20) = 0.45
        metrics = {
            "pod_restarts": [{"value": 0}],
            "oom_kills": [{"value": 1}],
            "cpu_usage": [{"value": 0.5}],
            "memory_percent": [{"value": 95}],
            "pod_status": [{"phase": "Running"}],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.25 + 0.20, abs=0.01)  # 0.45

    def test_max_score_capped_at_one(self) -> None:
        """Score should never exceed 1.0."""
        # All factors maxed out would exceed 1.0
        metrics = {
            "pod_restarts": [{"value": 10}],  # 0.35
            "oom_kills": [{"value": 5}],      # 0.25
            "cpu_usage": [{"value": 2.0}],    # (part of 0.20)
            "memory_percent": [{"value": 99}], # (part of 0.20)
            "pod_status": [{"phase": "Failed"}],  # 0.20
        }

        score = compute_infrastructure_score(metrics)
        assert score <= 1.0

    def test_empty_metrics_returns_low_score(self) -> None:
        """Empty metrics should return low score (no evidence)."""
        metrics = {
            "pod_restarts": [],
            "oom_kills": [],
            "cpu_usage": [],
            "memory_percent": [],
            "pod_status": [],
        }

        score = compute_infrastructure_score(metrics)
        assert score == pytest.approx(0.0, abs=0.01)

    def test_missing_metric_categories(self) -> None:
        """Missing metric categories should be handled gracefully."""
        metrics = {
            "pod_restarts": [{"value": 2}],
            # Missing other categories
        }

        # Should not raise, should handle missing keys
        score = compute_infrastructure_score(metrics)
        assert 0.0 <= score <= 1.0


class TestInfraPrometheusQueries:
    """Tests for INFRA_PROMETHEUS_QUERIES constant."""

    def test_queries_defined(self) -> None:
        """Ensure Prometheus queries are defined."""
        assert len(INFRA_PROMETHEUS_QUERIES) > 0

    def test_queries_are_strings(self) -> None:
        """All queries should be non-empty strings."""
        for query in INFRA_PROMETHEUS_QUERIES:
            assert isinstance(query, str)
            assert len(query) > 0

    def test_queries_target_5g_core_namespace(self) -> None:
        """Queries should target 5g-core namespace."""
        for query in INFRA_PROMETHEUS_QUERIES:
            assert "5g-core" in query or "namespace" in query


# TODO: Add tests for infra_agent() function after implementing async MCP client
# class TestInfraAgentFunction:
#     """Tests for infra_agent() entry point."""
#
#     @pytest.mark.asyncio
#     async def test_updates_state_correctly(
#         self, sample_initial_state, mock_mcp_client
#     ):
#         """Test that infra_agent updates state with findings."""
#         pass
#
#     @pytest.mark.asyncio
#     async def test_handles_mcp_timeout(self, sample_initial_state, mock_mcp_client):
#         """Test graceful handling of MCP timeout."""
#         pass
