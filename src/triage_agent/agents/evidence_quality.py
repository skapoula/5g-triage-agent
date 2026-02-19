"""Evidence quality scoring after data collection agents complete."""

from langsmith import traceable

from triage_agent.config import get_config
from triage_agent.state import TriageState


@traceable(name="EvidenceQuality")
def compute_evidence_quality(state: TriageState) -> TriageState:
    """Score evidence diversity. Runs after NfMetrics/NfLogs/UeTraces agents."""
    cfg = get_config()
    available_types = []
    if state.get("metrics"):
        available_types.append("metrics")
    if state.get("logs"):
        available_types.append("logs")
    if state.get("traces_ready"):
        available_types.append("traces")

    if len(available_types) == 3:
        quality_score = cfg.eq_score_all_sources       # Metrics + logs + traces
    elif len(available_types) == 2 and "traces" in available_types:
        quality_score = cfg.eq_score_traces_plus_one   # Traces + one other source
    elif len(available_types) == 2:
        quality_score = cfg.eq_score_metrics_logs      # Metrics + logs (no traces)
    elif "traces" in available_types:
        quality_score = cfg.eq_score_traces_only       # Traces only
    elif "metrics" in available_types:
        quality_score = cfg.eq_score_metrics_only      # Metrics only
    elif "logs" in available_types:
        quality_score = cfg.eq_score_logs_only         # Logs only
    else:
        quality_score = cfg.eq_score_no_evidence       # No evidence

    state["evidence_quality_score"] = min(quality_score, 1.0)
    return state
