"""LangGraph workflow definition for TriageAgent.

This module defines the directed acyclic graph (DAG) for the multi-agent
triage pipeline. The workflow coordinates specialized agents to investigate
5G core network failures and produce root cause analysis.

Pipeline:
    START → [InfraAgent | DataCollection] (parallel)
    DataCollection → NfMetricsAgent → NfLogsAgent → UeTracesAgent → EvidenceQuality
    [InfraAgent, EvidenceQuality] → RCAAgent
    RCAAgent → [confident?] → END or retry
"""

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from triage_agent.config import get_config
from triage_agent.state import TriageState


def should_retry(state: TriageState) -> Literal["retry", "finalize"]:
    """Determine if RCA should retry with more evidence."""
    needs_more = state.get("needs_more_evidence", False)
    attempt_count = state.get("attempt_count", 1)
    max_attempts = state.get("max_attempts", get_config().max_attempts)

    if needs_more and attempt_count < max_attempts:
        return "retry"
    return "finalize"


def increment_attempt(state: TriageState) -> TriageState:
    """Increment attempt counter before retry."""
    state["attempt_count"] = state.get("attempt_count", 1) + 1
    return state


def finalize_report(state: TriageState) -> TriageState:
    """Finalize the RCA report."""
    state["final_report"] = {
        "incident_id": state.get("incident_id"),
        "layer": state.get("layer"),
        "root_nf": state.get("root_nf"),
        "failure_mode": state.get("failure_mode"),
        "confidence": state.get("confidence"),
        "evidence_chain": state.get("evidence_chain", []),
        "infra_score": state.get("infra_score"),
        "evidence_quality_score": state.get("evidence_quality_score"),
        "degraded_mode": state.get("degraded_mode", False),
        "attempt_count": state.get("attempt_count", 1),
    }
    return state


def create_workflow() -> Any:
    """Create the TriageAgent LangGraph workflow.

    Returns:
        Compiled LangGraph workflow ready for execution.
    """
    # Import agents here to avoid circular imports
    from triage_agent.agents.evidence_quality import compute_evidence_quality
    from triage_agent.agents.infra_agent import infra_agent
    from triage_agent.agents.logs_agent import logs_agent
    from triage_agent.agents.metrics_agent import metrics_agent
    from triage_agent.agents.rca_agent import rca_agent_first_attempt
    from triage_agent.agents.ue_traces_agent import discover_and_trace_imsis

    # Create workflow with state schema
    workflow = StateGraph(TriageState)

    # Add all nodes
    workflow.add_node("infra_agent", infra_agent)
    workflow.add_node("metrics_agent", metrics_agent)
    workflow.add_node("logs_agent", logs_agent)
    workflow.add_node("traces_agent", discover_and_trace_imsis)
    workflow.add_node("evidence_quality", compute_evidence_quality)
    workflow.add_node("rca_agent", rca_agent_first_attempt)
    workflow.add_node("increment_attempt", increment_attempt)
    workflow.add_node("finalize", finalize_report)

    # Parallel start: InfraAgent and DataCollection run simultaneously
    workflow.add_edge(START, "infra_agent")
    workflow.add_edge(START, "metrics_agent")

    # Data collection pipeline (sequential within parallel branch)
    workflow.add_edge("metrics_agent", "logs_agent")
    workflow.add_edge("logs_agent", "traces_agent")
    workflow.add_edge("traces_agent", "evidence_quality")

    # Both branches must complete before RCA
    workflow.add_edge("infra_agent", "rca_agent")
    workflow.add_edge("evidence_quality", "rca_agent")

    # Conditional routing after RCA
    workflow.add_conditional_edges(
        "rca_agent",
        should_retry,
        {
            "retry": "increment_attempt",
            "finalize": "finalize",
        },
    )

    # Retry loop
    workflow.add_edge("increment_attempt", "rca_agent")

    # End state
    workflow.add_edge("finalize", END)

    return workflow.compile()


def get_initial_state(alert: dict[str, Any], incident_id: str) -> TriageState:
    """Create initial state from alert payload.

    Args:
        alert: Alertmanager alert payload
        incident_id: Unique investigation identifier

    Returns:
        Initialized TriageState ready for workflow execution.
    """
    return TriageState(
        alert=alert,
        incident_id=incident_id,
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
        max_attempts=get_config().max_attempts,
        needs_more_evidence=False,
        second_attempt_complete=False,
        final_report=None,
    )
