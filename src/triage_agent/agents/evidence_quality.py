"""Evidence quality scoring after data collection agents complete."""

from triage_agent.state import TriageState


def compute_evidence_quality(state: TriageState) -> TriageState:
    """Score evidence diversity. Runs after NfMetrics/NfLogs/UeTraces agents."""
    available_types = []
    if state.get("metrics"):
        available_types.append("metrics")
    if state.get("logs"):
        available_types.append("logs")
    if state.get("traces_ready"):
        available_types.append("traces")

    if len(available_types) == 3:
        quality_score = 0.95  # Metrics + logs + traces
    elif len(available_types) == 2 and "traces" in available_types:
        quality_score = 0.85  # Traces + one other source
    elif len(available_types) == 2:
        quality_score = 0.80  # Metrics + logs (no traces)
    elif "traces" in available_types:
        quality_score = 0.50  # Traces only
    elif "metrics" in available_types:
        quality_score = 0.40  # Metrics only
    elif "logs" in available_types:
        quality_score = 0.35  # Logs only
    else:
        quality_score = 0.10  # No evidence

    state["evidence_quality_score"] = min(quality_score, 1.0)
    return state
