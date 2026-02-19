"""InfraAgent: Infrastructure triage via Prometheus pod metrics.

Rule-based (no LLM). Queries Prometheus via MCP for pod-level health,
computes an infrastructure score, and forwards findings to RCAAgent.
"""

from datetime import datetime, timezone
from typing import Any

from langsmith import traceable

from triage_agent.config import get_config
from triage_agent.state import TriageState


def build_infra_queries(core_namespace: str) -> list[str]:
    """Build PromQL queries scoped to the given K8s namespace.

    The container/pod regex filter is derived from known_nfs plus mongodb
    (an Open5GS dependency that is monitored but is not a 5G NF itself).
    PromQL time windows are read from configuration.
    """
    cfg = get_config()
    ns = core_namespace
    # Build regex from known_nfs plus mongodb (infra dependency).
    nf_names = sorted(set(cfg.known_nfs) | {"mongodb"})
    pattern = "^(" + "|".join(nf_names) + ").*"
    cr = pattern
    pr = pattern
    rw = cfg.promql_restart_window
    ow = cfg.promql_oom_window
    cw = cfg.promql_cpu_rate_window_infra
    return [
        # Pod restarts (configurable window, default 1h)
        f'label_replace(sum by (namespace, pod, container)'
        f'(increase(kube_pod_container_status_restarts_total'
        f'{{namespace="{ns}", container=~"{cr}", '
        f'pod=~"{pr}"}}[{rw}])), '
        f'"report", "pod_restarts", "", "")',
        # OOM kills (configurable window, default 5m)
        f'label_replace(sum by (pod, container) '
        f'(increase(kube_pod_container_status_restarts_total'
        f'{{namespace="{ns}", container=~"{cr}", '
        f'pod=~"{pr}"}}[{ow}]) '
        f'* on(namespace, pod, container) group_left(reason) '
        f'kube_pod_container_status_last_terminated_reason{{reason="OOMKilled"}}), '
        f'"report", "oom_kills_5m", "", "")',
        # CPU usage rate (configurable window, default 2m)
        f'label_replace(sum by (pod, container) '
        f'(rate(container_cpu_usage_seconds_total'
        f'{{namespace="{ns}", container=~"{cr}", '
        f'pod=~"{pr}"}}[{cw}])), '
        f'"report", "cpu_usage_rate_2m", "", "")',
        # Memory usage percent
        f'label_replace((sum by (pod, container) '
        f'(container_memory_working_set_bytes'
        f'{{namespace="{ns}", container=~"{cr}", '
        f'pod=~"{pr}"}}) '
        f'/ sum by (pod, container) '
        f'(kube_pod_container_resource_limits{{resource="memory", namespace="{ns}", '
        f'container=~"{cr}", '
        f'pod=~"{pr}"}})) * 100, '
        f'"report", "memory_usage_percent", "", "")',
        # Pod status
        f'label_replace(sum by (namespace, pod, phase) '
        f'(kube_pod_status_phase{{namespace="{ns}", phase=~"Running|Pending|Unknown|Failed", '
        f'pod=~"{pr}"}}) > 0, '
        f'"__name__", "pod_status", "", "")',
    ]

# --- Infrastructure score: 4-factor weighted model ---
#
# | Factor                      | Weight | Scoring Logic                                        |
# |-----------------------------|--------|------------------------------------------------------|
# | Pod Reliability (Restarts)  | 0.35   | 0: 0.0, 1-2: 0.4, 3-5: 0.7, >5: 1.0               |
# | Critical Errors (OOM)       | 0.25   | 0: 0.0, >0: 1.0                                     |
# | Pod Health Status            | 0.20   | Running: 0.0, Pending: 0.6, Failed/Unknown: 1.0     |
# | Resource Saturation          | 0.20   | Mem>90%: 1.0, CPU>1.0core: 0.8, Normal: 0.0         |


def compute_infrastructure_score(metrics: dict[str, Any]) -> float:
    """Compute weighted infra score from pod metrics. Returns 0.0-1.0."""
    cfg = get_config()
    score = 0.0

    # Factor 1: Pod Restarts
    restarts = metrics.get("pod_restarts", [])
    max_restarts = max(
        (entry.get("value", 0) for entry in restarts), default=0
    )
    if max_restarts > cfg.restart_threshold_critical:
        restart_factor = 1.0
    elif max_restarts >= cfg.restart_threshold_high:
        restart_factor = cfg.restart_factor_high
    elif max_restarts >= 1:
        restart_factor = cfg.restart_factor_low
    else:
        restart_factor = 0.0
    score += cfg.infra_weight_restarts * restart_factor

    # Factor 2: OOM kills
    oom_kills = metrics.get("oom_kills", [])
    score += cfg.infra_weight_oom * (1.0 if oom_kills else 0.0)

    # Factor 3: Pod Status
    pod_status = metrics.get("pod_status", [])
    status_factor = 0.0
    for entry in pod_status:
        phase = entry.get("phase", "Running")
        if phase in ("Failed", "Unknown"):
            status_factor = max(status_factor, 1.0)
        elif phase == "Pending":
            status_factor = max(status_factor, cfg.pod_pending_factor)
    score += cfg.infra_weight_pod_status * status_factor

    # Factor 4: Resource Saturation
    memory_entries = metrics.get("memory_percent", [])
    cpu_entries = metrics.get("cpu_usage", [])
    max_mem = max(
        (entry.get("value", 0) for entry in memory_entries), default=0
    )
    max_cpu = max(
        (entry.get("value", 0) for entry in cpu_entries), default=0
    )
    if max_mem > cfg.memory_saturation_pct:
        resource_factor = 1.0
    elif max_cpu > cfg.cpu_saturation_cores:
        resource_factor = cfg.cpu_saturation_factor
    else:
        resource_factor = 0.0
    score += cfg.infra_weight_resources * resource_factor

    return min(score, 1.0)


def extract_restart_counts(metrics: dict[str, Any]) -> dict[str, int]:
    """Return {pod_name: restart_count} for all pods in pod_restarts."""
    result: dict[str, int] = {}
    for entry in metrics.get("pod_restarts", []):
        pod = entry.get("pod", "unknown")
        result[pod] = entry.get("value", 0)
    return result


def extract_oom_events(metrics: dict[str, Any]) -> dict[str, int]:
    """Return {pod_name: oom_count} for pods with OOM kills."""
    result: dict[str, int] = {}
    for entry in metrics.get("oom_kills", []):
        pod = entry.get("pod", "unknown")
        value = entry.get("value", 0)
        if value > 0:
            result[pod] = value
    return result


def extract_resource_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Return {pod_name: {"cpu": float, "memory_percent": float}} from resource metrics."""
    result: dict[str, dict[str, float]] = {}
    for entry in metrics.get("cpu_usage", []):
        pod = entry.get("pod", "unknown")
        if pod not in result:
            result[pod] = {"cpu": 0.0, "memory_percent": 0.0}
        result[pod]["cpu"] = entry.get("value", 0.0)
    for entry in metrics.get("memory_percent", []):
        pod = entry.get("pod", "unknown")
        if pod not in result:
            result[pod] = {"cpu": 0.0, "memory_percent": 0.0}
        result[pod]["memory_percent"] = entry.get("value", 0.0)
    return result


def extract_node_status(metrics: dict[str, Any]) -> dict[str, str]:
    """Return {pod_name: phase_string} from pod_status entries."""
    result: dict[str, str] = {}
    for entry in metrics.get("pod_status", []):
        pod = entry.get("pod", "unknown")
        result[pod] = entry.get("phase", "Unknown")
    return result


def count_concurrent_failures(metrics: dict[str, Any]) -> int:
    """Count distinct pods experiencing any failure condition."""
    failing_pods: set[str] = set()

    for entry in metrics.get("pod_restarts", []):
        if entry.get("value", 0) > 0:
            failing_pods.add(entry.get("pod", "unknown"))

    for entry in metrics.get("oom_kills", []):
        if entry.get("value", 0) > 0:
            failing_pods.add(entry.get("pod", "unknown"))

    for entry in metrics.get("pod_status", []):
        phase = entry.get("phase", "Running")
        if phase not in ("Running",):
            failing_pods.add(entry.get("pod", "unknown"))

    return len(failing_pods)


def extract_critical_events(metrics: dict[str, Any]) -> list[dict[str, object]]:
    """Identify critical infrastructure events (OOM, high restarts, failed pods)."""
    cfg = get_config()
    events: list[dict[str, object]] = []

    for entry in metrics.get("oom_kills", []):
        if entry.get("value", 0) > 0:
            events.append({
                "type": "oom_kill",
                "pod": entry.get("pod", "unknown"),
                "container": entry.get("container", ""),
                "value": entry.get("value", 0),
            })

    for entry in metrics.get("pod_restarts", []):
        if entry.get("value", 0) > cfg.restart_threshold_critical:
            events.append({
                "type": "excessive_restarts",
                "pod": entry.get("pod", "unknown"),
                "container": entry.get("container", ""),
                "value": entry.get("value", 0),
            })

    for entry in metrics.get("pod_status", []):
        phase = entry.get("phase", "Running")
        if phase in ("Failed", "Unknown", "CrashLoopBackOff"):
            events.append({
                "type": "pod_failure",
                "pod": entry.get("pod", "unknown"),
                "phase": phase,
            })

    return events


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp from alert payload. Returns Unix epoch seconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc if dt.tzinfo is None else dt.tzinfo).timestamp()


def extract_nfs_from_alert(alert: dict[str, Any]) -> list[str]:
    """Extract affected NF names from alert labels."""
    known_nfs = frozenset(get_config().known_nfs)
    labels = alert.get("labels", {})
    nfs: list[str] = []

    # Primary: explicit 'nf' label (may be comma-separated)
    nf_label = labels.get("nf", "")
    if nf_label:
        for part in nf_label.split(","):
            name = part.strip().lower()
            if name:
                nfs.append(name)

    # Fallback: extract NF prefix from pod name label
    if not nfs:
        pod_label = labels.get("pod", "")
        if pod_label:
            prefix = pod_label.split("-")[0].lower()
            if prefix in known_nfs:
                nfs.append(prefix)

    return nfs


@traceable(name="InfraAgent")
def infra_agent(state: TriageState) -> TriageState:
    """InfraAgent entry point. Rule-based, no LLM."""
    cfg = get_config()
    alert = state["alert"]

    alert_time = parse_timestamp(alert["startsAt"])
    time_window = (
        alert_time - cfg.alert_lookback_seconds,
        alert_time + cfg.alert_lookahead_seconds,
    )

    affected_nfs = extract_nfs_from_alert(alert)

    # MCP query to Prometheus
    # metrics = mcp_client.query_prometheus(queries=build_infra_queries(cfg.core_namespace), time_range=time_window)
    metrics: dict[str, Any] = {}  # TODO: wire up MCP client

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
