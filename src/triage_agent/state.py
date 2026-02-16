"""Shared state object for all agents in the triage pipeline."""

from typing import TypedDict


class TriageState(TypedDict):
    # Input
    alert: dict  # Alertmanager webhook payload

    # InfraAgent outputs (forwarded to RCAAgent)
    infra_checked: bool
    infra_score: float  # 0.0-1.0
    infra_findings: dict | None  # Pod metrics, events, resource usage

    # DAG mapping outputs (alert â†’ 3GPP procedure)
    procedure_name: str | None
    dag_id: str | None
    dag: dict | None  # Full DAG structure from Memgraph
    mapping_confidence: float  # Heuristic mapping confidence
    mapping_method: str  # "exact_match" | "keyword_match" | "nf_default" | "generic_fallback"

    # NfMetricsAgent/NfLogsAgent/UeTracesAgent outputs
    metrics: dict | None  # {nf_name: [metric_data]}
    logs: dict | None  # {nf_name: [log_entries]}
    discovered_imsis: list[str] | None  # IMSIs active in alarm window
    traces_ready: bool  # True when IMSI traces ingested into Memgraph
    trace_deviations: list[dict] | None  # Per-IMSI deviation results from Memgraph comparison
    incident_id: str  # Unique investigation identifier
    evidence_quality_score: float

    # RCAAgent outputs (considers infra + app evidence)
    root_nf: str | None
    failure_mode: str | None
    layer: str  # "infrastructure" or "application"
    confidence: float
    evidence_chain: list[dict]
    degraded_mode: bool  # True if LLM timeout triggered degraded mode
    degraded_reason: str | None  # Reason for degraded mode

    # Control flow
    attempt_count: int  # Current attempt (1-based)
    max_attempts: int  # Hard limit (default: 2)
    needs_more_evidence: bool
    second_attempt_complete: bool
    final_report: dict | None
