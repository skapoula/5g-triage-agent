"""Evidence quality scoring after data collection agents complete."""

from typing import Any

from langsmith import traceable

from triage_agent.config import get_config
from triage_agent.state import TriageState
from triage_agent.utils import save_artifact


@traceable(name="EvidenceQuality")
def compute_evidence_quality(state: TriageState) -> dict[str, Any]:
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

    score = min(quality_score, 1.0)
    save_artifact(
        state.get("incident_id", "unknown"),
        "evidence_quality.json",
        {
            "score": score,
            "sources": available_types,
            "metrics_present": "metrics" in available_types,
            "logs_present": "logs" in available_types,
            "traces_ready": "traces" in available_types,
        },
        cfg.artifacts_dir,
    )
    return {"evidence_quality_score": score}
