"""Full pipeline integration tests for the 5G triage multi-agent system.

Tests exercise the complete pipeline end-to-end using real Memgraph and
mocked Prometheus/Loki. Agent functions are called directly (not via
create_workflow) to avoid event loop conflicts with asyncio.run() in agents.

All test methods are sync `def` (not `async def`) so that asyncio.run()
inside agents creates its own event loop safely.

Run with:
    pytest tests/integration/test_full_pipeline.py -v --memgraph-url bolt://localhost:7687
"""
from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from triage_agent.agents.evidence_quality import compute_evidence_quality
from triage_agent.agents.infra_agent import infra_agent
from triage_agent.agents.logs_agent import logs_agent
from triage_agent.agents.metrics_agent import metrics_agent
from triage_agent.agents.rca_agent import rca_agent_first_attempt
from triage_agent.graph import (
    finalize_report,
    get_initial_state,
    increment_attempt,
    should_retry,
)
from triage_agent.memgraph.connection import MemgraphConnection

# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _make_rca_output(
    layer: str,
    root_nf: str,
    failure_mode: str,
    confidence: float,
    failed_phase: str | None = None,
) -> dict[str, Any]:
    """Return a minimal RCA output dict for mocking llm_analyze_evidence."""
    return {
        "layer": layer,
        "root_nf": root_nf,
        "failure_mode": failure_mode,
        "failed_phase": failed_phase,
        "confidence": confidence,
        "evidence_chain": [],
        "alternative_hypotheses": [],
        "reasoning": f"Mock reasoning for {layer}/{root_nf}",
    }


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def memgraph_url(request: pytest.FixtureRequest) -> str:
    """Get Memgraph URL from pytest options or environment variable."""
    try:
        return request.config.getoption("--memgraph-url")
    except ValueError:
        return os.environ.get("MEMGRAPH_URL", "bolt://localhost:7687")


@pytest.fixture(scope="module")
def memgraph_connection(memgraph_url: str) -> Generator[MemgraphConnection, None, None]:
    """Create Memgraph connection for integration tests."""
    conn = MemgraphConnection(memgraph_url)
    if not conn.health_check():
        pytest.skip(f"Memgraph not available at {memgraph_url}")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_test_data(memgraph_connection: MemgraphConnection) -> Generator[None, None, None]:
    """Clean up pipeline test data before and after each test."""
    memgraph_connection.execute_cypher_write(
        "MATCH (t:CapturedTrace) WHERE t.incident_id STARTS WITH 'test-pipeline-' DETACH DELETE t"
    )
    yield
    memgraph_connection.execute_cypher_write(
        "MATCH (t:CapturedTrace) WHERE t.incident_id STARTS WITH 'test-pipeline-' DETACH DELETE t"
    )


@pytest.fixture
def pipeline_dag() -> dict[str, Any]:
    """Registration_General DAG with merged schema compatible with all agents."""
    return {
        "name": "Registration_General",
        "spec": "TS 23.502 4.2.2.2.2",
        "procedure": "registration",
        "all_nfs": ["AMF", "AUSF", "UDM", "UPF", "NRF", "PCF"],
        "phases": [
            {
                "phase_id": "initial_registration",
                "actors": ["AMF"],
                "success_log": "Registration Request received",
                "failure_patterns": [],
                "order": 1,
                "nf": "AMF",
                "action": "Registration Request",
                "optional": False,
            },
            {
                "phase_id": "authentication",
                "actors": ["AMF", "AUSF"],
                "success_log": "Authentication successful",
                "failure_patterns": ["*auth*fail*", "*timeout*AUSF*", "*SUCI*decryption*"],
                "order": 9,
                "nf": "AMF",
                "action": "Authentication/Security",
                "optional": False,
            },
            {
                "phase_id": "registration_accept",
                "actors": ["AMF"],
                "success_log": "Registration Accept sent",
                "failure_patterns": ["*registration*reject*", "*accept*fail*"],
                "order": 21,
                "nf": "AMF",
                "action": "Registration Accept",
                "optional": False,
            },
        ],
    }


@pytest.fixture
def pdu_pipeline_dag() -> dict[str, Any]:
    """PDU_Session_Establishment DAG with merged schema compatible with all agents."""
    return {
        "name": "PDU_Session_Establishment",
        "spec": "TS 23.502 4.3.2.2.1",
        "procedure": "pdu_session",
        "all_nfs": ["AMF", "SMF", "UPF", "PCF", "UDM"],
        "phases": [
            {
                "phase_id": "pdu_request",
                "actors": ["AMF", "SMF"],
                "success_log": "PDU Session Establishment Request received",
                "failure_patterns": ["*PDU*reject*", "*session*fail*"],
                "order": 1,
                "nf": "AMF",
                "action": "PDU Session Establishment Request",
                "optional": False,
            },
            {
                "phase_id": "smf_selection",
                "actors": ["AMF", "SMF"],
                "success_log": "SMF selected",
                "failure_patterns": ["*SMF*not*found*", "*NF*discovery*fail*"],
                "order": 3,
                "nf": "SMF",
                "action": "SMF selection",
                "optional": False,
            },
            {
                "phase_id": "upf_tunnel",
                "actors": ["SMF", "UPF"],
                "success_log": "N4 Session Establishment successful",
                "failure_patterns": ["*UPF*fail*", "*tunnel*fail*", "*N4*timeout*"],
                "order": 5,
                "nf": "UPF",
                "action": "N4 Session Establishment",
                "optional": False,
            },
        ],
    }


# ──────────────────────────────────────────────────────────────────────────
# Alert payload builders
# ──────────────────────────────────────────────────────────────────────────


def _registration_alert() -> dict[str, Any]:
    return {
        "labels": {
            "alertname": "RegistrationFailureRate",
            "nf": "AMF",
            "severity": "critical",
            "namespace": "5g-core",
        },
        "annotations": {
            "summary": "High registration failure rate detected",
            "description": "AMF registration failure rate > 10%",
        },
        "startsAt": "2024-02-15T10:00:00Z",
        "endsAt": "2024-02-15T10:05:00Z",
    }


def _pdu_alert() -> dict[str, Any]:
    return {
        "labels": {
            "alertname": "PDUSessionFailureRate",
            "nf": "SMF",
            "severity": "critical",
            "namespace": "5g-core",
        },
        "annotations": {
            "summary": "High PDU session failure rate detected",
            "description": "SMF PDU session failure rate > 5%",
        },
        "startsAt": "2024-02-15T11:00:00Z",
        "endsAt": "2024-02-15T11:05:00Z",
    }


# ──────────────────────────────────────────────────────────────────────────
# Mock data builders
# ──────────────────────────────────────────────────────────────────────────


def _amf_error_metrics() -> list[dict[str, Any]]:
    """Prometheus results showing AMF with high error rate."""
    return [
        {"metric": {"pod": "amf-deployment-abc", "nf": "amf"}, "value": [1708000000, "15.5"]},
        {"metric": {"pod": "amf-deployment-abc", "nf": "amf"}, "value": [1708000001, "12.3"]},
    ]


def _healthy_metrics() -> list[dict[str, Any]]:
    """Prometheus results showing healthy NFs (low error rates)."""
    return [
        {"metric": {"pod": "amf-deployment-abc", "nf": "amf"}, "value": [1708000000, "0.1"]},
        {"metric": {"pod": "ausf-deployment-xyz", "nf": "ausf"}, "value": [1708000000, "0.2"]},
    ]


def _smf_error_metrics() -> list[dict[str, Any]]:
    """Prometheus results showing SMF/UPF with elevated error rates."""
    return [
        {"metric": {"pod": "smf-deployment-abc", "nf": "smf"}, "value": [1708000000, "8.5"]},
        {"metric": {"pod": "upf-deployment-xyz", "nf": "upf"}, "value": [1708000000, "5.1"]},
    ]


def _moderate_metrics() -> list[dict[str, Any]]:
    """Prometheus results with moderate/ambiguous values."""
    return [
        {"metric": {"pod": "amf-deployment-abc", "nf": "amf"}, "value": [1708000000, "2.5"]},
    ]


def _oom_logs() -> list[dict[str, Any]]:
    """Log entries showing OOMKill events at the AMF pod."""
    return [
        {
            "pod": "amf-deployment-abc",
            "message": "ERROR: OOMKilled - container memory limit exceeded",
            "level": "ERROR",
            "timestamp": 1708000000,
            "labels": {"k8s_pod_name": "amf-deployment-abc"},
        },
        {
            "pod": "amf-deployment-abc",
            "message": "ERROR: Pod restart due to memory pressure",
            "level": "ERROR",
            "timestamp": 1708000001,
            "labels": {"k8s_pod_name": "amf-deployment-abc"},
        },
    ]


def _ausf_auth_failure_logs() -> list[dict[str, Any]]:
    """Log entries showing AUSF authentication failures."""
    return [
        {
            "pod": "ausf-deployment-xyz",
            "message": "ERROR: Authentication failed - SUCI decryption error",
            "level": "ERROR",
            "timestamp": 1708000000,
            "labels": {"k8s_pod_name": "ausf-deployment-xyz"},
        },
        {
            "pod": "ausf-deployment-xyz",
            "message": "ERROR: timeout AUSF - 5G AKA authentication timeout",
            "level": "ERROR",
            "timestamp": 1708000001,
            "labels": {"k8s_pod_name": "ausf-deployment-xyz"},
        },
    ]


def _smf_upf_failure_logs() -> list[dict[str, Any]]:
    """Log entries showing SMF/UPF PDU session failure."""
    return [
        {
            "pod": "smf-deployment-abc",
            "message": "ERROR: UPF tunnel establishment failed - N4 timeout",
            "level": "ERROR",
            "timestamp": 1708003605,
            "labels": {"k8s_pod_name": "smf-deployment-abc"},
        },
    ]


def _sparse_logs() -> list[dict[str, Any]]:
    """Single WARN log entry (sparse evidence)."""
    return [
        {
            "pod": "amf-deployment-abc",
            "message": "WARN: Elevated registration latency",
            "level": "WARN",
            "timestamp": 1708000000,
            "labels": {"k8s_pod_name": "amf-deployment-abc"},
        },
    ]


# ──────────────────────────────────────────────────────────────────────────
# Scenario 1: Infrastructure root cause
# ──────────────────────────────────────────────────────────────────────────


class TestRegistrationFailureInfrastructureRootCause:
    """High infra_score → layer=infrastructure."""

    def test_registration_failure_infrastructure_root_cause(
        self,
        memgraph_connection: MemgraphConnection,
        pipeline_dag: dict[str, Any],
    ) -> None:
        incident_id = "test-pipeline-infra-001"
        alert = _registration_alert()

        state = get_initial_state(alert, incident_id=incident_id)
        state["dag"] = pipeline_dag
        state["procedure_name"] = "Registration_General"
        state["dag_id"] = "Registration_General"
        state["mapping_confidence"] = 0.95
        state["mapping_method"] = "exact_match"

        # InfraAgent: high infra score indicating pod-level issue
        with patch(
            "triage_agent.agents.infra_agent.compute_infrastructure_score",
            return_value=0.85,
        ):
            infra_agent(state)

        assert state["infra_score"] == pytest.approx(0.85)
        assert state["infra_checked"] is True

        # MetricsAgent: AMF with high error rate
        with patch(
            "triage_agent.agents.metrics_agent._fetch_prometheus_metrics",
            new=AsyncMock(return_value=_amf_error_metrics()),
        ):
            metrics_agent(state)

        assert state["metrics"] is not None
        assert len(state["metrics"]) > 0

        # LogsAgent: OOMKill/memory pressure logs at AMF
        with patch(
            "triage_agent.agents.logs_agent._check_mcp_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "triage_agent.agents.logs_agent._fetch_loki_logs",
            new=AsyncMock(return_value=_oom_logs()),
        ):
            logs_agent(state)

        assert state["logs"] is not None
        assert len(state["logs"]) > 0

        # UE traces: set directly — no IMSI discovery for infra scenario
        state["discovered_imsis"] = []
        state["traces_ready"] = False
        state["trace_deviations"] = []

        # EvidenceQuality: metrics + logs only → 0.80
        compute_evidence_quality(state)

        assert state["evidence_quality_score"] == pytest.approx(0.80)

        # RCAAgent: LLM returns infrastructure root cause
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=_make_rca_output(
                layer="infrastructure",
                root_nf="AMF",
                failure_mode="OOMKilled",
                confidence=0.88,
            ),
        ):
            rca_agent_first_attempt(state)

        assert state["layer"] == "infrastructure"
        assert state["root_nf"] in ["AMF", "UPF", "pod-level", "amf"]
        assert state["confidence"] > 0.60
        assert state["infra_score"] > 0.80
        assert state["needs_more_evidence"] is False

        state = finalize_report(state)

        assert state["final_report"] is not None
        assert state["final_report"]["layer"] == "infrastructure"
        assert state["final_report"]["incident_id"] == incident_id


# ──────────────────────────────────────────────────────────────────────────
# Scenario 2: Application root cause
# ──────────────────────────────────────────────────────────────────────────


class TestRegistrationFailureApplicationRootCause:
    """Low infra_score + AUSF auth errors + trace deviation → layer=application."""

    def test_registration_failure_application_root_cause(
        self,
        memgraph_connection: MemgraphConnection,
        pipeline_dag: dict[str, Any],
    ) -> None:
        incident_id = "test-pipeline-app-001"
        test_imsi = "001010000000001"
        alert = _registration_alert()

        # Skip if Registration DAG not loaded in Memgraph
        dag_in_memgraph = memgraph_connection.load_reference_dag("Registration_General")
        if dag_in_memgraph is None:
            pytest.skip("Registration_General DAG not loaded in Memgraph")

        # Ingest a captured trace with authentication failure at step 9
        events = [
            {
                "order": 1,
                "action": "Registration Request",
                "timestamp": 1708000000,
                "nf": "UE",
            },
            {
                "order": 9,
                "action": "Authentication FAILED - SUCI decryption error",
                "timestamp": 1708000009,
                "nf": "AMF",
            },
        ]
        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi=test_imsi,
            events=events,
        )

        deviation = memgraph_connection.detect_deviation(
            incident_id=incident_id,
            imsi=test_imsi,
            dag_name="Registration_General",
        )
        trace_deviations = [deviation] if deviation else []

        state = get_initial_state(alert, incident_id=incident_id)
        state["dag"] = pipeline_dag
        state["procedure_name"] = "Registration_General"
        state["dag_id"] = "Registration_General"
        state["mapping_confidence"] = 0.95
        state["mapping_method"] = "exact_match"

        # InfraAgent: low infra score — no pod-level issues
        with patch(
            "triage_agent.agents.infra_agent.compute_infrastructure_score",
            return_value=0.15,
        ):
            infra_agent(state)

        assert state["infra_score"] == pytest.approx(0.15)

        # MetricsAgent: healthy NF metrics
        with patch(
            "triage_agent.agents.metrics_agent._fetch_prometheus_metrics",
            new=AsyncMock(return_value=_healthy_metrics()),
        ):
            metrics_agent(state)

        assert state["metrics"] is not None

        # LogsAgent: AUSF authentication failure logs
        with patch(
            "triage_agent.agents.logs_agent._check_mcp_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "triage_agent.agents.logs_agent._fetch_loki_logs",
            new=AsyncMock(return_value=_ausf_auth_failure_logs()),
        ):
            logs_agent(state)

        assert state["logs"] is not None

        # Inject pre-detected deviations directly (bypass discover_and_trace_imsis)
        state["discovered_imsis"] = [test_imsi]
        state["traces_ready"] = True
        state["trace_deviations"] = trace_deviations

        # EvidenceQuality: metrics + logs + traces → 0.95
        compute_evidence_quality(state)

        assert state["evidence_quality_score"] == pytest.approx(0.95)

        # RCAAgent: LLM returns application root cause at AUSF
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=_make_rca_output(
                layer="application",
                root_nf="AUSF",
                failure_mode="authentication_failure",
                confidence=0.82,
                failed_phase="authentication",
            ),
        ):
            rca_agent_first_attempt(state)

        assert state["layer"] == "application"
        assert state["root_nf"] == "AUSF"
        assert state["confidence"] > 0.60
        assert state["infra_score"] < 0.40
        assert state["needs_more_evidence"] is False

        state = finalize_report(state)

        assert state["final_report"]["root_nf"] == "AUSF"
        assert state["final_report"]["layer"] == "application"


# ──────────────────────────────────────────────────────────────────────────
# Scenario 3: PDU session failure with trace deviation
# ──────────────────────────────────────────────────────────────────────────


class TestPDUSessionFailureWithTraceDeviation:
    """PDU session failure with UPF tunnel deviation detected in Memgraph."""

    def test_pdu_session_failure_with_trace_deviation(
        self,
        memgraph_connection: MemgraphConnection,
        pdu_pipeline_dag: dict[str, Any],
    ) -> None:
        incident_id = "test-pipeline-pdu-001"
        test_imsi = "001010000000002"
        alert = _pdu_alert()

        # Skip if PDU DAG not loaded in Memgraph
        pdu_dag = memgraph_connection.load_reference_dag("PDU_Session_Establishment")
        if pdu_dag is None:
            pytest.skip("PDU_Session_Establishment DAG not loaded in Memgraph")

        # Ingest a trace with UPF tunnel failure at step 5
        events = [
            {
                "order": 1,
                "action": "PDU Session Establishment Request",
                "timestamp": 1708003600,
                "nf": "AMF",
            },
            {
                "order": 3,
                "action": "SMF selection",
                "timestamp": 1708003601,
                "nf": "SMF",
            },
            {
                "order": 5,
                "action": "UPF tunnel FAILED - N4 session establishment error",
                "timestamp": 1708003605,
                "nf": "UPF",
            },
        ]
        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi=test_imsi,
            events=events,
        )

        deviation = memgraph_connection.detect_deviation(
            incident_id=incident_id,
            imsi=test_imsi,
            dag_name="PDU_Session_Establishment",
        )
        trace_deviations = [deviation] if deviation else []

        state = get_initial_state(alert, incident_id=incident_id)
        state["dag"] = pdu_pipeline_dag
        state["procedure_name"] = "PDU_Session_Establishment"
        state["dag_id"] = "PDU_Session_Establishment"
        state["mapping_confidence"] = 0.95
        state["mapping_method"] = "exact_match"

        # InfraAgent: low infra score
        with patch(
            "triage_agent.agents.infra_agent.compute_infrastructure_score",
            return_value=0.20,
        ):
            infra_agent(state)

        # MetricsAgent: SMF/UPF error metrics
        with patch(
            "triage_agent.agents.metrics_agent._fetch_prometheus_metrics",
            new=AsyncMock(return_value=_smf_error_metrics()),
        ):
            metrics_agent(state)

        # LogsAgent: SMF/UPF failure logs
        with patch(
            "triage_agent.agents.logs_agent._check_mcp_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "triage_agent.agents.logs_agent._fetch_loki_logs",
            new=AsyncMock(return_value=_smf_upf_failure_logs()),
        ):
            logs_agent(state)

        # Inject pre-detected deviations directly
        state["discovered_imsis"] = [test_imsi]
        state["traces_ready"] = True
        state["trace_deviations"] = trace_deviations

        # EvidenceQuality
        compute_evidence_quality(state)

        # RCAAgent: LLM returns SMF application root cause
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=_make_rca_output(
                layer="application",
                root_nf="SMF",
                failure_mode="pdu_session_failure",
                confidence=0.79,
                failed_phase="upf_tunnel",
            ),
        ):
            rca_agent_first_attempt(state)

        assert state["trace_deviations"] is not None
        assert state["layer"] == "application"
        assert state["root_nf"] == "SMF"

        if trace_deviations:
            assert "deviation_point" in state["trace_deviations"][0]
            assert "expected" in state["trace_deviations"][0]
            assert "actual" in state["trace_deviations"][0]

        state = finalize_report(state)

        assert state["final_report"]["layer"] == "application"
        assert state["final_report"]["root_nf"] == "SMF"


# ──────────────────────────────────────────────────────────────────────────
# Scenario 4: Low confidence triggers retry loop
# ──────────────────────────────────────────────────────────────────────────


class TestLowConfidenceTriggersRetry:
    """First attempt confidence < threshold → retry → final report on second attempt."""

    def test_low_confidence_triggers_retry(
        self,
        memgraph_connection: MemgraphConnection,
        pipeline_dag: dict[str, Any],
    ) -> None:
        incident_id = "test-pipeline-retry-001"
        alert = _registration_alert()

        state = get_initial_state(alert, incident_id=incident_id)
        state["dag"] = pipeline_dag
        state["procedure_name"] = "Registration_General"
        state["dag_id"] = "Registration_General"
        state["mapping_confidence"] = 0.75
        state["mapping_method"] = "keyword_match"

        # InfraAgent: ambiguous score (neither clearly infra nor app)
        with patch(
            "triage_agent.agents.infra_agent.compute_infrastructure_score",
            return_value=0.42,
        ):
            infra_agent(state)

        # MetricsAgent: moderate/ambiguous metrics
        with patch(
            "triage_agent.agents.metrics_agent._fetch_prometheus_metrics",
            new=AsyncMock(return_value=_moderate_metrics()),
        ):
            metrics_agent(state)

        # LogsAgent: sparse logs — only a single WARN
        with patch(
            "triage_agent.agents.logs_agent._check_mcp_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "triage_agent.agents.logs_agent._fetch_loki_logs",
            new=AsyncMock(return_value=_sparse_logs()),
        ):
            logs_agent(state)

        # UE traces: set directly — no IMSI discovery
        state["discovered_imsis"] = []
        state["traces_ready"] = False
        state["trace_deviations"] = []

        # EvidenceQuality: metrics + logs (no traces) → 0.80
        compute_evidence_quality(state)

        assert state["evidence_quality_score"] == pytest.approx(0.80)

        # RCA First Attempt: low confidence (0.45 < 0.65 threshold) → needs retry
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=_make_rca_output(
                layer="application",
                root_nf="AMF",
                failure_mode="unknown",
                confidence=0.45,
            ),
        ):
            rca_agent_first_attempt(state)

        assert state["confidence"] < 0.70
        assert state["needs_more_evidence"] is True
        assert state["evidence_gaps"] is not None
        assert len(state["evidence_gaps"]) > 0
        assert should_retry(state) == "retry"

        # Increment attempt counter
        state = increment_attempt(state)
        assert state["attempt_count"] == 2

        # RCA Second Attempt: confidence above threshold → no retry
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            return_value=_make_rca_output(
                layer="application",
                root_nf="AMF",
                failure_mode="registration_failure",
                confidence=0.72,
                failed_phase="authentication",
            ),
        ):
            rca_agent_first_attempt(state)

        assert state["confidence"] >= 0.70
        assert state["needs_more_evidence"] is False
        assert should_retry(state) == "finalize"

        # Finalize: produces standardized report with attempt_count
        state = finalize_report(state)

        assert state["final_report"] is not None
        assert state["final_report"]["attempt_count"] == 2
        assert state["final_report"]["incident_id"] == incident_id
        assert state["final_report"]["confidence"] >= 0.70
