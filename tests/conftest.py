"""Pytest configuration and fixtures for TriageAgent tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from triage_agent.mcp.client import MCPClient
from triage_agent.memgraph.connection import MemgraphConnection
from triage_agent.state import TriageState


@pytest.fixture
def mock_mcp_client() -> AsyncMock:
    """Mock MCP client for unit tests."""
    client = AsyncMock(spec=MCPClient)
    client.query_prometheus.return_value = {"result": []}
    client.query_prometheus_range.return_value = {"result": []}
    client.query_loki.return_value = []
    client.health_check_prometheus.return_value = True
    client.health_check_loki.return_value = True
    return client


@pytest.fixture
def mock_memgraph() -> MagicMock:
    """Mock Memgraph connection for unit tests."""
    conn = MagicMock(spec=MemgraphConnection)
    conn.execute_cypher.return_value = []
    conn.health_check.return_value = True
    conn.load_reference_dag.return_value = None
    conn.detect_deviation.return_value = None
    return conn


@pytest.fixture
def sample_alert() -> dict[str, Any]:
    """Sample Alertmanager webhook payload."""
    return {
        "status": "firing",
        "labels": {
            "alertname": "RegistrationFailures",
            "severity": "critical",
            "namespace": "5g-core",
            "nf": "amf",
        },
        "annotations": {
            "summary": "Registration failures detected",
            "description": "AMF registration failure rate exceeded threshold",
        },
        "startsAt": "2026-02-15T10:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus:9090/graph",
        "fingerprint": "abc123",
    }


@pytest.fixture
def sample_dag() -> dict[str, Any]:
    """Sample DAG structure for registration procedure."""
    return {
        "name": "Registration_General",
        "spec": "TS 23.502 4.2.2.2.2",
        "procedure": "registration",
        "all_nfs": ["AMF", "AUSF", "UDM", "NRF", "PCF"],
        "phases": [
            {
                "order": 1,
                "nf": "UE",
                "action": "Registration Request",
                "keywords": ["Registration Request", "Initial Registration", "SUCI"],
                "optional": False,
            },
            {
                "order": 9,
                "nf": "AMF",
                "action": "Authentication/Security",
                "keywords": ["Authentication", "Security", "AUSF", "AKA"],
                "sub_dag": "Authentication_5G_AKA",
                "optional": False,
                "failure_patterns": ["*auth*fail*", "*timeout*AUSF*"],
            },
            {
                "order": 21,
                "nf": "AMF",
                "action": "Registration Accept",
                "keywords": ["Registration Accept"],
                "optional": False,
                "success_log": "Registration Accept sent",
                "failure_patterns": ["*registration*reject*", "*accept*fail*"],
            },
        ],
    }


@pytest.fixture
def sample_initial_state(sample_alert: dict[str, Any]) -> TriageState:
    """Sample initial TriageState for testing."""
    return TriageState(
        alert=sample_alert,
        incident_id="test-incident-001",
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


@pytest.fixture
def sample_prometheus_metrics() -> dict[str, Any]:
    """Sample Prometheus query response."""
    return {
        "resultType": "vector",
        "result": [
            {
                "metric": {
                    "pod": "amf-deployment-abc123",
                    "container": "amf",
                    "namespace": "5g-core",
                    "report": "pod_restarts",
                },
                "value": [1708000000, "2"],
            },
            {
                "metric": {
                    "pod": "ausf-deployment-def456",
                    "container": "ausf",
                    "namespace": "5g-core",
                    "report": "pod_restarts",
                },
                "value": [1708000000, "0"],
            },
        ],
    }


@pytest.fixture
def sample_loki_logs() -> list[dict[str, Any]]:
    """Sample Loki query response."""
    return [
        {
            "timestamp": 1708000000,
            "message": "ERROR [AMF] Authentication failed for IMSI 001010123456789",
            "labels": {"pod": "amf-deployment-abc123", "namespace": "5g-core"},
            "pod": "amf-deployment-abc123",
            "level": "ERROR",
        },
        {
            "timestamp": 1708000001,
            "message": "WARN [AUSF] Timeout waiting for UDM response",
            "labels": {"pod": "ausf-deployment-def456", "namespace": "5g-core"},
            "pod": "ausf-deployment-def456",
            "level": "WARN",
        },
    ]


@pytest.fixture
def sample_trace_deviation() -> dict[str, Any]:
    """Sample trace deviation from Memgraph."""
    return {
        "deviation_point": 9,
        "expected": "Authentication/Security",
        "actual": "Authentication failed",
        "expected_nf": "AMF",
        "actual_nf": "AMF",
    }
