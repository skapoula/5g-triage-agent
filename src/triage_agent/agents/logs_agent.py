"""NfLogsAgent: Per-NF log collection from Loki via MCP.

No LLM. Queries Loki for ERROR/WARN/FATAL logs and phase-specific patterns
from the candidate NF list provided by the DAG.
"""

import re

from triage_agent.state import TriageState


def parse_timestamp(ts: str):
    raise NotImplementedError


def extract_nf_from_pod_name(pod: str) -> str:
    raise NotImplementedError


def wildcard_match(text: str, pattern: str) -> bool:
    """Case-insensitive wildcard matching. '*' matches any characters."""
    regex_pattern = pattern.replace("*", ".*")
    return bool(re.search(f"(?i){regex_pattern}", text))


def organize_and_annotate_logs(logs: list[dict], dag: dict) -> dict:
    """Organize logs by NF and annotate with matched phase/pattern."""
    organized: dict = {}

    for log_entry in logs:
        nf = extract_nf_from_pod_name(log_entry["pod"])
        message = log_entry["message"]

        if nf not in organized:
            organized[nf] = []

        matched_phase: str | None = None
        matched_pattern: str | None = None

        for phase in dag["phases"]:
            for pattern in phase.get("failure_patterns", []):
                if wildcard_match(message, pattern):
                    matched_phase = phase["phase_id"]
                    matched_pattern = pattern
                    break
            if matched_phase:
                break

        organized[nf].append({
            "level": log_entry["level"],
            "message": message,
            "timestamp": log_entry["timestamp"],
            "matched_phase": matched_phase,
            "matched_pattern": matched_pattern,
        })

    return organized


def logs_agent(state: TriageState) -> TriageState:
    """NfLogsAgent entry point. Pure MCP query, no LLM."""
    dag = state["dag"]
    alert_time = parse_timestamp(state["alert"]["startsAt"])
    time_window = (alert_time - 300, alert_time + 60)  # -5min to +60s

    queries = []
    for nf in dag["all_nfs"]:
        nf_lower = nf.lower()

        # Base query: all ERROR/WARN/FATAL logs
        queries.append(
            f'{{namespace="5g-core",pod=~".*{nf_lower}.*"}} |~ "ERROR|WARN|FATAL"'
        )

        # Phase-specific pattern queries
        for phase in dag["phases"]:
            if nf in phase["actors"]:
                queries.append(
                    f'{{namespace="5g-core",pod=~".*{nf_lower}.*"}} |~ "{phase["success_log"]}"'
                )
                for pattern in phase.get("failure_patterns", []):
                    loki_pattern = pattern.replace("*", ".*")
                    queries.append(
                        f'{{namespace="5g-core",pod=~".*{nf_lower}.*"}} |~ "(?i){loki_pattern}"'
                    )

    # Execute parallel MCP queries
    # logs = mcp_client.query_loki(queries=queries, time_range=time_window)
    logs_raw: list = []  # TODO: wire up MCP client

    state["logs"] = organize_and_annotate_logs(logs_raw, dag)
    return state
