"""NfMetricsAgent: Per-NF Prometheus metrics collection via MCP.

No LLM. Queries Prometheus for error rates, latency, CPU, memory per NF
from the candidate list provided by the DAG.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from langsmith import traceable

from triage_agent.mcp.client import MCPClient, MCPQueryError, MCPTimeoutError
from triage_agent.state import TriageState

logger = logging.getLogger(__name__)


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp from alert payload. Returns Unix epoch seconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(
        tzinfo=UTC if dt.tzinfo is None else dt.tzinfo
    ).timestamp()


def _resolve_nf(
    metric_labels: dict[str, str], nfs_lower: dict[str, str]
) -> str | None:
    """Determine which NF a Prometheus result belongs to.

    Checks 'nf' label first, then extracts prefix from 'pod' label.
    Returns the original-case NF name or None if unresolvable.
    """
    # Direct nf label
    nf_label = metric_labels.get("nf", "").lower()
    if nf_label in nfs_lower:
        return nfs_lower[nf_label]

    # Pod name prefix (e.g. "amf-deployment-abc123" -> "amf")
    pod_label = metric_labels.get("pod", "")
    if pod_label:
        prefix = pod_label.split("-")[0].lower()
        if prefix in nfs_lower:
            return nfs_lower[prefix]

    return None


def organize_metrics_by_nf(
    metrics: list[dict[str, Any]], nfs: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Group a flat list of Prometheus result entries by NF name.

    Args:
        metrics: List of Prometheus result entries, each with 'metric' and 'value'.
        nfs: List of NF names (e.g. ["AMF", "AUSF"]) from the DAG.

    Returns:
        {NF_NAME: [result_entries...]} grouped by resolved NF.
    """
    # Build lowercase -> original-case lookup
    nfs_lower: dict[str, str] = {nf.lower(): nf for nf in nfs}
    result: dict[str, list[dict[str, Any]]] = {}

    for entry in metrics:
        labels = entry.get("metric", {})
        nf_name = _resolve_nf(labels, nfs_lower)
        if nf_name is None:
            continue
        if nf_name not in result:
            result[nf_name] = []
        result[nf_name].append(entry)

    return result


def build_nf_queries(nfs: list[str]) -> list[str]:
    """Build PromQL queries for each NF: error_rate, p95_latency, cpu, memory.

    Args:
        nfs: List of NF names from the DAG (e.g. ["AMF", "AUSF"]).

    Returns:
        List of PromQL query strings (4 per NF).
    """
    queries: list[str] = []
    for nf in nfs:
        nf_lower = nf.lower()
        queries.extend([
            f'rate(http_requests_total{{nf="{nf_lower}",status=~"5.."}}[1m])',
            f'histogram_quantile(0.95, http_request_duration_seconds{{nf="{nf_lower}"}})',
            f'rate(container_cpu_usage_seconds_total{{pod=~".*{nf_lower}.*"}}[5m])',
            f'container_memory_working_set_bytes{{pod=~".*{nf_lower}.*"}}',
        ])
    return queries


async def _fetch_prometheus_metrics(
    queries: list[str],
    alert_time: int,
) -> list[dict[str, Any]]:
    """Execute PromQL queries via MCP client, collecting results per-query.

    Each query is executed individually so that a single failure does not
    discard results from other queries (graceful partial failure).

    Args:
        queries: PromQL query strings to execute.
        alert_time: Unix epoch seconds for the query timestamp.

    Returns:
        Flat list of Prometheus result entries across all successful queries.
    """
    if not queries:
        return []

    results: list[dict[str, Any]] = []
    async with MCPClient() as client:
        for query in queries:
            try:
                data = await client.query_prometheus(query, time=alert_time)
                results.extend(data.get("result", []))
            except MCPTimeoutError:
                logger.warning("Prometheus query timed out: %s", query)
            except MCPQueryError as exc:
                logger.warning("Prometheus query failed: %s — %s", query, exc)

    return results


@traceable(name="NfMetricsAgent")
def metrics_agent(state: TriageState) -> TriageState:
    """NfMetricsAgent entry point. Pure MCP query, no LLM."""
    dag = state["dag"]
    assert dag is not None, "metrics_agent requires DAG in state"
    alert_time = parse_timestamp(state["alert"]["startsAt"])

    queries = build_nf_queries(dag["all_nfs"])

    # Fetch metrics from Prometheus via MCP (graceful degradation on failure)
    raw_results: list[dict[str, Any]] = []
    if queries:
        try:
            raw_results = asyncio.run(
                _fetch_prometheus_metrics(queries, alert_time=int(alert_time))
            )
        except Exception:
            logger.warning(
                "MCP client unavailable, proceeding with empty metrics",
                exc_info=True,
            )

    state["metrics"] = organize_metrics_by_nf(raw_results, dag["all_nfs"])
    return state
