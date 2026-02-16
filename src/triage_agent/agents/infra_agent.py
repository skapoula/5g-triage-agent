"""InfraAgent: Infrastructure triage via Prometheus pod metrics.

Rule-based (no LLM). Queries Prometheus via MCP for pod-level health,
computes an infrastructure score, and forwards findings to RCAAgent.
"""

from triage_agent.state import TriageState

# --- Prometheus queries for pod-level metrics ---

INFRA_PROMETHEUS_QUERIES = [
    # Pod restarts (1h window)
    'label_replace(sum by (namespace, pod, container)'
    '(increase(kube_pod_container_status_restarts_total'
    '{namespace="5g-core", container=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*", '
    'pod=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"}[1h])), '
    '"report", "pod_restarts", "", "")',
    # OOM kills (5m window)
    'label_replace(sum by (pod, container) '
    '(increase(kube_pod_container_status_restarts_total'
    '{namespace="5g-core", container=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*", '
    'pod=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"}[5m]) '
    '* on(namespace, pod, container) group_left(reason) '
    'kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}), '
    '"report", "oom_kills_5m", "", "")',
    # CPU usage rate (2m window)
    'label_replace(sum by (pod, container) '
    '(rate(container_cpu_usage_seconds_total'
    '{namespace="5g-core", container=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*", '
    'pod=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"}[2m])), '
    '"report", "cpu_usage_rate_2m", "", "")',
    # Memory usage percent
    'label_replace((sum by (pod, container) '
    '(container_memory_working_set_bytes'
    '{namespace="5g-core", container=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*", '
    'pod=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"}) '
    '/ sum by (pod, container) '
    '(kube_pod_container_resource_limits{resource="memory", namespace="5g-core", '
    'container=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*", '
    'pod=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"})) * 100, '
    '"report", "memory_usage_percent", "", "")',
    # Pod status
    'label_replace(sum by (namespace, pod, phase) '
    '(kube_pod_status_phase{namespace="5g-core", phase=~"Running|Pending|Unknown|Failed", '
    'pod=~"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"}) > 0, '
    '"__name__", "pod_status", "", "")',
]

# --- Infrastructure score: 4-factor weighted model ---
#
# | Factor                      | Weight | Scoring Logic                                        |
# |-----------------------------|--------|------------------------------------------------------|
# | Pod Reliability (Restarts)  | 0.35   | 0: 0.0, 1-2: 0.4, 3-5: 0.7, >5: 1.0               |
# | Critical Errors (OOM)       | 0.25   | 0: 0.0, >0: 1.0                                     |
# | Pod Health Status            | 0.20   | Running: 0.0, Pending: 0.6, Failed/Unknown: 1.0     |
# | Resource Saturation          | 0.20   | Mem>90%: 1.0, CPU>1.0core: 0.8, Normal: 0.0         |


def compute_infrastructure_score(metrics: dict) -> float:
    """Compute weighted infra score from pod metrics. Returns 0.0-1.0."""
    score = 0.0

    # Factor 1: Pod Restarts (weight 0.35)
    restarts = metrics.get("pod_restarts", [])
    max_restarts = max(
        (entry.get("value", 0) for entry in restarts), default=0
    )
    if max_restarts > 5:
        restart_factor = 1.0
    elif max_restarts >= 3:
        restart_factor = 0.7
    elif max_restarts >= 1:
        restart_factor = 0.4
    else:
        restart_factor = 0.0
    score += 0.35 * restart_factor

    # Factor 2: OOM kills (weight 0.25)
    oom_kills = metrics.get("oom_kills", [])
    score += 0.25 * (1.0 if oom_kills else 0.0)

    # Factor 3: Pod Status (weight 0.20)
    pod_status = metrics.get("pod_status", [])
    status_factor = 0.0
    for entry in pod_status:
        phase = entry.get("phase", "Running")
        if phase in ("Failed", "Unknown"):
            status_factor = max(status_factor, 1.0)
        elif phase == "Pending":
            status_factor = max(status_factor, 0.6)
    score += 0.20 * status_factor

    # Factor 4: Resource Saturation (weight 0.20)
    memory_entries = metrics.get("memory_percent", [])
    cpu_entries = metrics.get("cpu_usage", [])
    max_mem = max(
        (entry.get("value", 0) for entry in memory_entries), default=0
    )
    max_cpu = max(
        (entry.get("value", 0) for entry in cpu_entries), default=0
    )
    if max_mem > 90:
        resource_factor = 1.0
    elif max_cpu > 1.0:
        resource_factor = 0.8
    else:
        resource_factor = 0.0
    score += 0.20 * resource_factor

    return min(score, 1.0)


def extract_restart_counts(metrics: dict) -> dict:
    raise NotImplementedError


def extract_oom_events(metrics: dict) -> dict:
    raise NotImplementedError


def extract_resource_metrics(metrics: dict) -> dict:
    raise NotImplementedError


def extract_node_status(metrics: dict) -> dict:
    raise NotImplementedError


def count_concurrent_failures(metrics: dict) -> int:
    raise NotImplementedError


def extract_critical_events(metrics: dict) -> list:
    raise NotImplementedError


def parse_timestamp(ts: str):
    """Parse ISO timestamp from alert payload."""
    raise NotImplementedError


def extract_nfs_from_alert(alert: dict) -> list[str]:
    """Extract affected NF names from alert labels."""
    raise NotImplementedError


def infra_agent(state: TriageState) -> TriageState:
    """InfraAgent entry point. Rule-based, no LLM."""
    alert = state["alert"]

    alert_time = parse_timestamp(alert["startsAt"])
    time_window = (alert_time - 300, alert_time + 60)  # -5min to +60s

    affected_nfs = extract_nfs_from_alert(alert)

    # MCP query to Prometheus
    # metrics = mcp_client.query_prometheus(queries=INFRA_PROMETHEUS_QUERIES, time_range=time_window)
    metrics = {}  # TODO: wire up MCP client

    infra_score = compute_infrastructure_score(metrics)

    # Always forward findings to RCAAgent (no early exit)
    state["infra_checked"] = True
    state["infra_score"] = infra_score
    state["infra_findings"] = {
        "pod_restarts": extract_restart_counts(metrics),
        "oom_kills": extract_oom_events(metrics),
        "resource_usage": extract_resource_metrics(metrics),
        "node_health": extract_node_status(metrics),
        "concurrent_failures": count_concurrent_failures(metrics),
        "critical_events": extract_critical_events(metrics),
    }

    return state
