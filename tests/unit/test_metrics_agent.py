"""Tests for NfMetricsAgent - test-first development.

Covers:
  - parse_timestamp
  - _resolve_nf (via organize_metrics_by_nf)
  - organize_metrics_by_nf
  - metrics_agent entry point
  - PromQL query construction (4 query types per NF)
  - Partial metric failures (graceful handling)
  - Empty NF list handling
"""

from typing import Any

import pytest

from triage_agent.agents.metrics_agent import (
    _resolve_nf,
    build_nf_queries,
    metrics_agent,
    organize_metrics_by_nf,
    parse_timestamp,
)
from triage_agent.state import TriageState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_prometheus_results() -> list[dict[str, Any]]:
    """Flat list of Prometheus result entries across multiple NFs/metrics.

    Simulates MCP client returning results for queries:
      - rate(http_requests_total{nf="amf",status=~"5.."}[1m])
      - histogram_quantile(0.95, http_request_duration_seconds{nf="amf"})
      - rate(container_cpu_usage_seconds_total{pod=~".*amf.*"}[5m])
      - container_memory_working_set_bytes{pod=~".*amf.*"}
      (... repeated for ausf)
    """
    return [
        # AMF error rate
        {
            "metric": {"__name__": "http_requests_total", "nf": "amf", "status": "503"},
            "value": [1708000000, "0.05"],
        },
        # AMF latency p95
        {
            "metric": {"__name__": "http_request_duration_seconds", "nf": "amf"},
            "value": [1708000000, "0.250"],
        },
        # AMF CPU
        {
            "metric": {
                "__name__": "container_cpu_usage_seconds_total",
                "pod": "amf-deployment-abc123",
                "namespace": "5g-core",
            },
            "value": [1708000000, "0.45"],
        },
        # AMF memory
        {
            "metric": {
                "__name__": "container_memory_working_set_bytes",
                "pod": "amf-deployment-abc123",
                "namespace": "5g-core",
            },
            "value": [1708000000, "134217728"],
        },
        # AUSF error rate
        {
            "metric": {"__name__": "http_requests_total", "nf": "ausf", "status": "500"},
            "value": [1708000000, "0.12"],
        },
        # AUSF latency p95
        {
            "metric": {"__name__": "http_request_duration_seconds", "nf": "ausf"},
            "value": [1708000000, "1.500"],
        },
        # AUSF CPU
        {
            "metric": {
                "__name__": "container_cpu_usage_seconds_total",
                "pod": "ausf-deployment-def456",
                "namespace": "5g-core",
            },
            "value": [1708000000, "0.80"],
        },
        # AUSF memory
        {
            "metric": {
                "__name__": "container_memory_working_set_bytes",
                "pod": "ausf-deployment-def456",
                "namespace": "5g-core",
            },
            "value": [1708000000, "268435456"],
        },
    ]


@pytest.fixture
def partial_prometheus_results() -> list[dict[str, Any]]:
    """Prometheus results where only some NFs returned data.

    Simulates: AMF returned all 4 metric types, AUSF returned only error_rate,
    UDM returned nothing (absent from results entirely).
    """
    return [
        # AMF - all 4 metric types present
        {
            "metric": {"__name__": "http_requests_total", "nf": "amf", "status": "503"},
            "value": [1708000000, "0.05"],
        },
        {
            "metric": {"__name__": "http_request_duration_seconds", "nf": "amf"},
            "value": [1708000000, "0.250"],
        },
        {
            "metric": {
                "__name__": "container_cpu_usage_seconds_total",
                "pod": "amf-deployment-abc123",
            },
            "value": [1708000000, "0.45"],
        },
        {
            "metric": {
                "__name__": "container_memory_working_set_bytes",
                "pod": "amf-deployment-abc123",
            },
            "value": [1708000000, "134217728"],
        },
        # AUSF - only error rate returned (latency/cpu/memory queries failed)
        {
            "metric": {"__name__": "http_requests_total", "nf": "ausf", "status": "500"},
            "value": [1708000000, "0.12"],
        },
        # UDM - completely absent (all queries returned empty)
    ]


@pytest.fixture
def empty_dag() -> dict[str, Any]:
    """DAG with an empty NF list."""
    return {
        "name": "Empty_Procedure",
        "spec": "N/A",
        "procedure": "unknown",
        "all_nfs": [],
        "phases": [],
    }


@pytest.fixture
def single_nf_dag() -> dict[str, Any]:
    """DAG with a single NF."""
    return {
        "name": "Single_NF",
        "spec": "N/A",
        "procedure": "test",
        "all_nfs": ["AMF"],
        "phases": [],
    }


# ===========================================================================
# parse_timestamp
# ===========================================================================


class TestParseTimestamp:
    """Tests for parse_timestamp(). Returns Unix epoch (int or float)."""

    def test_parses_iso8601_utc(self) -> None:
        result = parse_timestamp("2026-02-15T10:00:00Z")
        assert isinstance(result, (int, float))
        assert result == pytest.approx(1771149600, abs=1)

    def test_parses_with_fractional_seconds(self) -> None:
        result = parse_timestamp("2026-02-15T10:00:00.500Z")
        assert isinstance(result, (int, float))
        assert result == pytest.approx(1771149600.5, abs=1)

    def test_supports_arithmetic(self) -> None:
        """Used as alert_time - 300, must be numeric."""
        result = parse_timestamp("2026-02-15T10:00:00Z")
        assert (result - 300) < result

    def test_invalid_raises(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            parse_timestamp("garbage")


# ===========================================================================
# organize_metrics_by_nf
# ===========================================================================


class TestOrganizeMetricsByNf:
    """Tests for organize_metrics_by_nf().

    Contract:
      Input:  flat list of Prometheus result entries, list of NF names
      Output: {NF_NAME: [metric_entries...]} grouped by NF
    """

    def test_returns_dict(
        self, raw_prometheus_results: list[dict[str, Any]]
    ) -> None:
        result = organize_metrics_by_nf(raw_prometheus_results, ["AMF", "AUSF"])
        assert isinstance(result, dict)

    def test_keys_are_nf_names(
        self, raw_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """Output keys should be NF names from the provided list."""
        nfs = ["AMF", "AUSF"]
        result = organize_metrics_by_nf(raw_prometheus_results, nfs)
        for key in result:
            assert key.upper() in [n.upper() for n in nfs]

    def test_groups_amf_metrics(
        self, raw_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """AMF should have metrics collected from nf=amf and pod=amf-*."""
        result = organize_metrics_by_nf(raw_prometheus_results, ["AMF", "AUSF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        amf_metrics = result[amf_key]
        assert isinstance(amf_metrics, list)
        assert len(amf_metrics) >= 4  # error_rate, latency, cpu, memory

    def test_groups_ausf_metrics(
        self, raw_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """AUSF should have its own set of metrics."""
        result = organize_metrics_by_nf(raw_prometheus_results, ["AMF", "AUSF"])
        ausf_key = next(k for k in result if k.upper() == "AUSF")
        assert len(result[ausf_key]) >= 4

    def test_resolves_nf_from_nf_label(self) -> None:
        """Metrics with explicit 'nf' label should map to that NF."""
        entries = [
            {"metric": {"__name__": "http_requests_total", "nf": "amf"}, "value": [0, "1"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        assert len(result[amf_key]) == 1

    def test_resolves_nf_from_pod_label(self) -> None:
        """Metrics without 'nf' label should resolve NF from pod name prefix."""
        entries = [
            {
                "metric": {
                    "__name__": "container_cpu_usage_seconds_total",
                    "pod": "smf-deployment-xyz",
                },
                "value": [0, "0.5"],
            },
        ]
        result = organize_metrics_by_nf(entries, ["SMF"])
        smf_key = next(k for k in result if k.upper() == "SMF")
        assert len(result[smf_key]) == 1

    def test_unresolvable_metrics_excluded(self) -> None:
        """Entries that can't be mapped to any NF should be excluded."""
        entries = [
            {"metric": {"__name__": "random_metric"}, "value": [0, "1"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        total = sum(len(v) for v in result.values())
        assert total == 0

    def test_empty_results(self) -> None:
        """Empty metrics list should return empty dict (or dict with empty lists)."""
        result = organize_metrics_by_nf([], ["AMF", "AUSF"])
        total = sum(len(v) for v in result.values()) if result else 0
        assert total == 0

    def test_empty_nfs_list(self) -> None:
        """No NFs to match should return empty dict."""
        entries = [
            {"metric": {"nf": "amf"}, "value": [0, "1"]},
        ]
        result = organize_metrics_by_nf(entries, [])
        assert result == {} or sum(len(v) for v in result.values()) == 0

    def test_preserves_metric_values(
        self, raw_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """Each grouped entry should preserve the original metric data."""
        result = organize_metrics_by_nf(raw_prometheus_results, ["AMF", "AUSF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        for entry in result[amf_key]:
            assert "metric" in entry or "value" in entry

    def test_nf_label_takes_precedence_over_pod(self) -> None:
        """When both 'nf' and 'pod' labels exist, 'nf' should be used."""
        entries = [
            {
                "metric": {
                    "__name__": "http_requests_total",
                    "nf": "ausf",
                    "pod": "amf-deployment-xyz",
                },
                "value": [0, "1"],
            },
        ]
        result = organize_metrics_by_nf(entries, ["AMF", "AUSF"])
        # Should be grouped under AUSF (nf label), not AMF (pod prefix)
        assert any(k.upper() == "AUSF" for k in result)
        ausf_key = next(k for k in result if k.upper() == "AUSF")
        assert len(result[ausf_key]) == 1
        # AMF should have nothing (or not exist)
        amf_entries = sum(
            len(v) for k, v in result.items() if k.upper() == "AMF"
        )
        assert amf_entries == 0

    def test_case_insensitive_nf_matching(self) -> None:
        """NF resolution should be case-insensitive."""
        entries = [
            {"metric": {"nf": "AMF"}, "value": [0, "1"]},
            {"metric": {"nf": "Amf"}, "value": [0, "2"]},
            {"metric": {"nf": "amf"}, "value": [0, "3"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        assert len(result[amf_key]) == 3

    def test_preserves_original_case_in_keys(self) -> None:
        """Output keys should use the original-case NF name from the input list."""
        entries = [
            {"metric": {"nf": "amf"}, "value": [0, "1"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        assert "AMF" in result


# ===========================================================================
# Partial metric failures
# ===========================================================================


class TestPartialMetricFailures:
    """Tests for graceful handling when some NFs or metric types are missing.

    Simulates real-world scenarios:
      - Prometheus returns data for some NFs but not others
      - Only a subset of metric types available for an NF
      - MCP returns empty results entirely
    """

    def test_partial_nf_data_includes_available(
        self, partial_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """NFs with data should still appear in output."""
        result = organize_metrics_by_nf(
            partial_prometheus_results, ["AMF", "AUSF", "UDM"]
        )
        assert any(k.upper() == "AMF" for k in result)
        assert any(k.upper() == "AUSF" for k in result)

    def test_partial_nf_data_missing_nf_absent(
        self, partial_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """NFs with no data should be absent from the result dict."""
        result = organize_metrics_by_nf(
            partial_prometheus_results, ["AMF", "AUSF", "UDM"]
        )
        udm_entries = sum(
            len(v) for k, v in result.items() if k.upper() == "UDM"
        )
        assert udm_entries == 0

    def test_partial_metrics_per_nf(
        self, partial_prometheus_results: list[dict[str, Any]]
    ) -> None:
        """NF with only some metric types should have fewer entries than full data."""
        result = organize_metrics_by_nf(
            partial_prometheus_results, ["AMF", "AUSF", "UDM"]
        )
        amf_key = next(k for k in result if k.upper() == "AMF")
        ausf_key = next(k for k in result if k.upper() == "AUSF")
        # AMF has all 4 types, AUSF has only 1
        assert len(result[amf_key]) == 4
        assert len(result[ausf_key]) == 1

    def test_no_crash_on_empty_metric_labels(self) -> None:
        """Entries with empty metric labels should be silently skipped."""
        entries = [
            {"metric": {}, "value": [0, "1"]},
            {"metric": {"nf": "amf"}, "value": [0, "2"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        assert len(result[amf_key]) == 1

    def test_no_crash_on_missing_metric_key(self) -> None:
        """Entries missing the 'metric' key entirely should be skipped."""
        entries = [
            {"value": [0, "1"]},  # no 'metric' key
            {"metric": {"nf": "amf"}, "value": [0, "2"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        assert len(result[amf_key]) == 1

    def test_no_crash_on_missing_value_key(self) -> None:
        """Entries missing 'value' should still be grouped (value not used for grouping)."""
        entries = [
            {"metric": {"nf": "amf"}},  # no 'value' key
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        assert len(result[amf_key]) == 1

    def test_mixed_nf_and_pod_resolution_partial(self) -> None:
        """Some entries resolve via 'nf' label, others via 'pod' prefix."""
        entries = [
            {"metric": {"nf": "amf"}, "value": [0, "1"]},
            {"metric": {"pod": "amf-deployment-xyz"}, "value": [0, "2"]},
            {"metric": {"pod": "unknown-service-123"}, "value": [0, "3"]},
        ]
        result = organize_metrics_by_nf(entries, ["AMF"])
        amf_key = next(k for k in result if k.upper() == "AMF")
        assert len(result[amf_key]) == 2  # nf + pod, not "unknown"


# ===========================================================================
# _resolve_nf (tested directly for edge cases)
# ===========================================================================


class TestResolveNf:
    """Direct tests for _resolve_nf() edge cases."""

    def test_returns_original_case_nf(self) -> None:
        nfs_lower = {"amf": "AMF", "ausf": "AUSF"}
        result = _resolve_nf({"nf": "amf"}, nfs_lower)
        assert result == "AMF"

    def test_returns_none_for_unknown_nf(self) -> None:
        nfs_lower = {"amf": "AMF"}
        result = _resolve_nf({"nf": "unknown"}, nfs_lower)
        assert result is None

    def test_pod_prefix_fallback(self) -> None:
        nfs_lower = {"smf": "SMF"}
        result = _resolve_nf({"pod": "smf-deployment-abc"}, nfs_lower)
        assert result == "SMF"

    def test_nf_label_priority_over_pod(self) -> None:
        nfs_lower = {"amf": "AMF", "ausf": "AUSF"}
        result = _resolve_nf({"nf": "ausf", "pod": "amf-deployment-x"}, nfs_lower)
        assert result == "AUSF"

    def test_empty_labels_returns_none(self) -> None:
        result = _resolve_nf({}, {"amf": "AMF"})
        assert result is None

    def test_empty_nfs_lower_returns_none(self) -> None:
        result = _resolve_nf({"nf": "amf"}, {})
        assert result is None

    def test_pod_name_with_many_dashes(self) -> None:
        """Pod names like 'udm-v2-deployment-abc123-xyz' — prefix is 'udm'."""
        nfs_lower = {"udm": "UDM"}
        result = _resolve_nf({"pod": "udm-v2-deployment-abc123-xyz"}, nfs_lower)
        assert result == "UDM"


# ===========================================================================
# metrics_agent entry point
# ===========================================================================


class TestMetricsAgentFunction:
    """Tests for metrics_agent() entry point."""

    def test_sets_metrics_in_state(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        """metrics_agent must populate state['metrics']."""
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        assert result["metrics"] is not None
        assert isinstance(result["metrics"], dict)

    def test_returns_triage_state(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        assert isinstance(result, dict)
        assert "alert" in result

    def test_does_not_modify_other_fields(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        """Should not touch fields owned by other agents."""
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        assert result["root_nf"] is None
        assert result["logs"] is None
        assert result["infra_checked"] is False

    def test_preserves_alert_in_state(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        """Alert payload should pass through unchanged."""
        state = sample_initial_state
        state["dag"] = sample_dag
        original_alert = state["alert"].copy()
        result = metrics_agent(state)
        assert result["alert"] == original_alert

    def test_preserves_incident_id(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        assert result["incident_id"] == "test-incident-001"

    def test_asserts_on_none_dag(
        self,
        sample_initial_state: TriageState,
    ) -> None:
        """Should raise AssertionError if dag is None."""
        state = sample_initial_state
        state["dag"] = None
        with pytest.raises(AssertionError, match="metrics_agent requires DAG"):
            metrics_agent(state)

    def test_metrics_is_dict_not_list(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        """state['metrics'] must be a dict keyed by NF name, not a flat list."""
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        assert isinstance(result["metrics"], dict)
        assert not isinstance(result["metrics"], list)


# ===========================================================================
# metrics_agent with empty NF list
# ===========================================================================


class TestMetricsAgentEmptyNfs:
    """Tests for metrics_agent when dag['all_nfs'] is empty."""

    def test_empty_nfs_produces_empty_metrics(
        self,
        sample_initial_state: TriageState,
        empty_dag: dict[str, Any],
    ) -> None:
        """With no NFs in DAG, state['metrics'] should be empty."""
        state = sample_initial_state
        state["dag"] = empty_dag
        result = metrics_agent(state)
        assert result["metrics"] is not None
        assert isinstance(result["metrics"], dict)
        total = sum(len(v) for v in result["metrics"].values())
        assert total == 0

    def test_empty_nfs_no_queries_generated(
        self,
        sample_initial_state: TriageState,
        empty_dag: dict[str, Any],
    ) -> None:
        """With no NFs, zero Prometheus queries should be built."""
        state = sample_initial_state
        state["dag"] = empty_dag
        # Should complete without error
        result = metrics_agent(state)
        assert result["metrics"] == {}

    def test_empty_nfs_does_not_crash(
        self,
        sample_initial_state: TriageState,
        empty_dag: dict[str, Any],
    ) -> None:
        """Graceful handling — no exception."""
        state = sample_initial_state
        state["dag"] = empty_dag
        result = metrics_agent(state)
        assert isinstance(result, dict)


# ===========================================================================
# metrics_agent state["metrics"] correctness
# ===========================================================================


class TestMetricsAgentStateUpdate:
    """Verify state['metrics'] is correctly structured after metrics_agent runs.

    Because MCP is not yet wired, the stub returns empty results.
    These tests validate the structural contract so that once MCP
    is connected, the output shape is already verified.
    """

    def test_metrics_value_is_dict_keyed_by_nf(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        metrics = result["metrics"]
        assert isinstance(metrics, dict)
        # Keys (if any) should be valid NF names from the DAG
        valid_nfs = {nf.upper() for nf in sample_dag["all_nfs"]}
        for key in metrics:
            assert key.upper() in valid_nfs

    def test_metrics_values_are_lists(
        self,
        sample_initial_state: TriageState,
        sample_dag: dict[str, Any],
    ) -> None:
        state = sample_initial_state
        state["dag"] = sample_dag
        result = metrics_agent(state)
        for nf_name, entries in result["metrics"].items():
            assert isinstance(entries, list), (
                f"metrics[{nf_name!r}] should be a list"
            )

    def test_single_nf_dag_metrics_structure(
        self,
        sample_initial_state: TriageState,
        single_nf_dag: dict[str, Any],
    ) -> None:
        """With a single-NF DAG, metrics should have at most one NF key."""
        state = sample_initial_state
        state["dag"] = single_nf_dag
        result = metrics_agent(state)
        # With stub (empty results), metrics is {}; with real MCP, key would be "AMF"
        for key in result["metrics"]:
            assert key.upper() == "AMF"


# ===========================================================================
# PromQL query construction
# ===========================================================================


class TestBuildNfQueries:
    """Tests for build_nf_queries() — extracted PromQL query builder."""

    def test_generates_four_queries_per_nf(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """Should generate 4 queries per NF (error rate, latency, CPU, memory)."""
        queries = build_nf_queries(sample_dag["all_nfs"])
        assert len(queries) == len(sample_dag["all_nfs"]) * 4
        assert len(queries) == 20  # 5 NFs * 4 queries

    def test_queries_use_lowercase_nf_names(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """PromQL queries should use lowercase NF names for label matching."""
        queries = build_nf_queries(sample_dag["all_nfs"])
        for nf in sample_dag["all_nfs"]:
            nf_lower = nf.lower()
            nf_queries = [q for q in queries if nf_lower in q]
            # All queries for this NF use lowercase
            for q in nf_queries:
                assert nf not in q or nf == nf_lower  # uppercase NF name not in query

    def test_error_rate_query_pattern(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """Error rate query should use rate() over http_requests_total with 5xx filter."""
        queries = build_nf_queries(sample_dag["all_nfs"])
        error_queries = [q for q in queries if "http_requests_total" in q]
        assert len(error_queries) == len(sample_dag["all_nfs"])
        for q in error_queries:
            assert "rate(" in q
            assert 'status=~"5.."' in q

    def test_p95_latency_query_pattern(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """P95 latency query should use histogram_quantile(0.95, ...)."""
        queries = build_nf_queries(sample_dag["all_nfs"])
        latency_queries = [q for q in queries if "histogram_quantile" in q]
        assert len(latency_queries) == len(sample_dag["all_nfs"])
        for q in latency_queries:
            assert "histogram_quantile(0.95" in q
            assert "http_request_duration_seconds" in q

    def test_cpu_query_pattern(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """CPU query should use rate() over container_cpu_usage_seconds_total with pod regex."""
        queries = build_nf_queries(sample_dag["all_nfs"])
        cpu_queries = [q for q in queries if "container_cpu_usage_seconds_total" in q]
        assert len(cpu_queries) == len(sample_dag["all_nfs"])
        for q in cpu_queries:
            assert "rate(" in q
            assert "[5m]" in q

    def test_memory_query_pattern(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """Memory query should use container_memory_working_set_bytes with pod regex."""
        queries = build_nf_queries(sample_dag["all_nfs"])
        mem_queries = [q for q in queries if "container_memory_working_set_bytes" in q]
        assert len(mem_queries) == len(sample_dag["all_nfs"])

    def test_each_nf_has_all_four_types(
        self, sample_dag: dict[str, Any]
    ) -> None:
        """Each NF should have exactly one of each query type."""
        queries = build_nf_queries(sample_dag["all_nfs"])

        for nf in sample_dag["all_nfs"]:
            nf_lower = nf.lower()
            nf_queries = [q for q in queries if nf_lower in q]
            assert len(nf_queries) == 4, f"{nf} should have exactly 4 queries"

            types_found = set()
            for q in nf_queries:
                if "http_requests_total" in q and "rate(" in q:
                    types_found.add("error_rate")
                elif "histogram_quantile" in q:
                    types_found.add("p95_latency")
                elif "container_cpu_usage_seconds_total" in q:
                    types_found.add("cpu")
                elif "container_memory_working_set_bytes" in q:
                    types_found.add("memory")

            expected = {"error_rate", "p95_latency", "cpu", "memory"}
            assert types_found == expected, (
                f"{nf} missing query types: {expected - types_found}"
            )

    def test_empty_nfs_produces_no_queries(self) -> None:
        """Empty NF list should produce zero PromQL queries."""
        queries = build_nf_queries([])
        assert len(queries) == 0

    def test_single_nf(self) -> None:
        """Single NF should produce exactly 4 queries."""
        queries = build_nf_queries(["AMF"])
        assert len(queries) == 4
        assert all("amf" in q for q in queries)
