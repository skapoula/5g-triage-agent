"""NfMetricsAgent: Per-NF Prometheus metrics collection via MCP.

No LLM. Queries Prometheus for error rates, latency, CPU, memory per NF
from the candidate list provided by the DAG.
"""

from triage_agent.state import TriageState


def parse_timestamp(ts: str):
    raise NotImplementedError


def organize_metrics_by_nf(metrics: list, nfs: list[str]) -> dict:
    raise NotImplementedError


def metrics_agent(state: TriageState) -> TriageState:
    """NfMetricsAgent entry point. Pure MCP query, no LLM."""
    dag = state["dag"]
    alert_time = parse_timestamp(state["alert"]["startsAt"])
    time_window = (alert_time - 300, alert_time + 60)  # -5min to +60s

    # Build Prometheus queries for each NF
    queries = []
    for nf in dag["all_nfs"]:
        nf_lower = nf.lower()
        queries.extend([
            f'rate(http_requests_total{{nf="{nf_lower}",status=~"5.."}}[1m])',
            f'histogram_quantile(0.95, http_request_duration_seconds{{nf="{nf_lower}"}})',
            f'rate(container_cpu_usage_seconds_total{{pod=~".*{nf_lower}.*"}}[5m])',
            f'container_memory_working_set_bytes{{pod=~".*{nf_lower}.*"}}',
        ])

    # Execute parallel MCP queries
    # metrics = mcp_client.query_prometheus(queries=queries, time_range=time_window)
    metrics = {}  # TODO: wire up MCP client

    state["metrics"] = organize_metrics_by_nf(metrics, dag["all_nfs"])
    return state
