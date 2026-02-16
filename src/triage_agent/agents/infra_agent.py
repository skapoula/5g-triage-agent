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
    # TODO: implement scoring logic per weight table above
    raise NotImplementedError


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
