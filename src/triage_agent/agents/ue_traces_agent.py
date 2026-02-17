"""UeTracesAgent: IMSI trace discovery, construction, and Memgraph ingestion.

No LLM. Discovers active IMSIs in the alarm window via Loki, constructs
per-IMSI traces, ingests them into Memgraph, and runs deviation detection
against reference DAGs.

Two-path architecture (mirrors NfLogsAgent):
  1. Upfront health check — probe MCP server /ready endpoint.
  2. If MCP reachable → fetch logs via MCP client.
  3. If MCP unreachable → fetch logs via direct Loki HTTP API.

Pipeline:
  1. IMSI discovery pass (Loki query)
  2. Per-IMSI trace construction
  3. Memgraph ingestion + comparison against reference DAG
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from langsmith import traceable

from triage_agent.config import get_config
from triage_agent.mcp.client import MCPClient
from triage_agent.memgraph.connection import get_memgraph
from triage_agent.state import TriageState

logger = logging.getLogger(__name__)

# IMSI pattern: case-insensitive "imsi-" followed by exactly 15 digits
_IMSI_PATTERN = re.compile(r"(?i)imsi-(\d{15})")


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp from alert payload. Returns Unix epoch seconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(
        tzinfo=UTC if dt.tzinfo is None else dt.tzinfo
    ).timestamp()


def extract_nf_from_pod_name(pod: str) -> str:
    """Extract NF name prefix from a k8s pod name. Returns lowercase."""
    return pod.split("-")[0].lower()


# ---------------------------------------------------------------------------
# Pure functions (no I/O)
# ---------------------------------------------------------------------------


def extract_unique_imsis(logs: list[dict[str, Any]]) -> list[str]:
    """Scan log messages for IMSI pattern 'imsi-<15 digits>'.

    Returns deduplicated list of IMSI digit strings (no 'imsi-' prefix).
    Preserves discovery order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for entry in logs:
        message = entry.get("message", "")
        for match in _IMSI_PATTERN.finditer(message):
            imsi = match.group(1)
            if imsi not in seen:
                seen.add(imsi)
                result.append(imsi)
    return result


def per_imsi_logql(imsi: str) -> str:
    """Build LogQL query for a specific IMSI in the 5g-core namespace."""
    return f'{{k8s_namespace_name="5g-core"}} |~ "{imsi}"'


def contract_imsi_trace(
    raw_trace: list[dict[str, Any]], imsi: str
) -> dict[str, Any]:
    """Contract raw log entries into a structured trace dict for Memgraph.

    Returns:
        {"imsi": str, "events": [{timestamp, nf, message}, ...]}
        Events are sorted chronologically by timestamp.
    """
    events: list[dict[str, Any]] = []
    for entry in raw_trace:
        events.append({
            "timestamp": entry.get("timestamp", 0),
            "nf": extract_nf_from_pod_name(entry.get("pod", "unknown")),
            "message": entry.get("message", ""),
        })
    events.sort(key=lambda e: e["timestamp"])
    return {"imsi": imsi, "events": events}


# ---------------------------------------------------------------------------
# Loki two-path: MCP + direct HTTP
# ---------------------------------------------------------------------------


async def _check_mcp_available() -> bool:
    """Lightweight MCP health check: probe Loki /ready via MCP server."""
    try:
        async with MCPClient() as client:
            return await client.health_check_loki()
    except Exception:
        return False


async def _fetch_loki_logs_mcp(
    query: str,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    """Fetch logs from Loki via MCP client."""
    async with MCPClient() as client:
        logs = await client.query_loki(query, start=start, end=end)

    # Normalize pod field: MCPClient reads labels["pod"] which may be empty
    # when Loki uses k8s-style labels (k8s_pod_name).
    for entry in logs:
        if not entry.get("pod"):
            entry["pod"] = entry.get("labels", {}).get("k8s_pod_name", "")

    return logs


def _extract_log_level(message: str) -> str:
    """Extract log level from message text."""
    message_upper = message.upper()
    for level in ("FATAL", "ERROR", "WARN", "INFO", "DEBUG"):
        if level in message_upper:
            return level
    return "INFO"


def _parse_loki_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Loki query_range JSON response into flat log entry list."""
    logs: list[dict[str, Any]] = []
    for stream in data.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for value in stream.get("values", []):
            logs.append({
                "timestamp": int(value[0]) // 1_000_000_000,
                "message": value[1],
                "labels": labels,
                "pod": labels.get("k8s_pod_name", labels.get("pod", "")),
                "level": _extract_log_level(value[1]),
            })
    return logs


async def _fetch_loki_logs_direct(
    query: str,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    """Fetch logs directly from Loki HTTP API, bypassing MCP server."""
    config = get_config()
    async with httpx.AsyncClient(timeout=config.mcp_timeout) as client:
        try:
            response = await client.get(
                f"{config.loki_url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": start * 1_000_000_000,
                    "end": end * 1_000_000_000,
                    "limit": 1000,
                },
            )
            response.raise_for_status()
            return _parse_loki_response(response.json())
        except httpx.TimeoutException:
            logger.warning("Loki direct query timed out: %s", query)
        except httpx.HTTPStatusError as exc:
            logger.warning("Loki direct query HTTP error: %s — %s", query, exc)
        except Exception:
            logger.warning("Loki direct query failed: %s", query, exc_info=True)
    return []


def loki_query(logql: str, start: int, end: int) -> list[dict[str, Any]]:
    """Execute a Loki query with two-path architecture.

    1. Probe MCP server availability (lightweight /ready check).
    2. If reachable → MCP path.
    3. If unreachable → direct Loki HTTP path.
    """
    # Step 1: Health check
    try:
        use_mcp = asyncio.run(_check_mcp_available())
    except Exception:
        logger.warning(
            "MCP health check failed, defaulting to direct Loki",
            exc_info=True,
        )
        use_mcp = False

    # Step 2: Execute query on chosen path
    if use_mcp:
        try:
            return asyncio.run(
                _fetch_loki_logs_mcp(logql, start=start, end=end)
            )
        except Exception:
            logger.warning(
                "MCP query failed despite passing health check,"
                " returning empty results",
                exc_info=True,
            )
            return []
    else:
        logger.info("MCP server unavailable, using direct Loki connection")
        try:
            return asyncio.run(
                _fetch_loki_logs_direct(logql, start=start, end=end)
            )
        except Exception:
            logger.warning(
                "Direct Loki query failed, returning empty results",
                exc_info=True,
            )
            return []


# ---------------------------------------------------------------------------
# Memgraph interactions
# ---------------------------------------------------------------------------


def ingest_traces_to_memgraph(
    traces: list[dict[str, Any]], incident_id: str
) -> None:
    """Ingest per-IMSI traces into Memgraph as :CapturedTrace nodes."""
    if not traces:
        return

    conn = get_memgraph()
    for trace in traces:
        conn.ingest_captured_trace(
            incident_id,
            trace["imsi"],
            trace["events"],
        )


def run_deviation_detection(
    incident_id: str, dag_name: str
) -> list[dict[str, Any]]:
    """Compare ingested traces against reference DAG in Memgraph.

    Queries all captured IMSIs for this incident, then runs per-IMSI
    deviation detection against the reference DAG.

    Returns list of deviation dicts (one per IMSI that deviates).
    """
    conn = get_memgraph()

    # Get all captured IMSIs for this incident
    imsi_records = conn.execute_cypher(
        "MATCH (t:CapturedTrace {incident_id: $incident_id}) "
        "RETURN t.imsi AS imsi",
        {"incident_id": incident_id},
    )

    deviations: list[dict[str, Any]] = []
    for record in imsi_records:
        imsi = record["imsi"]
        deviation = conn.detect_deviation(incident_id, imsi, dag_name)
        if deviation is not None:
            deviations.append(deviation)

    return deviations


# ---------------------------------------------------------------------------
# Agent entry point
# ---------------------------------------------------------------------------


@traceable(name="UeTracesAgent")
def discover_and_trace_imsis(state: TriageState) -> TriageState:
    """UeTracesAgent entry point. Pure MCP query + Memgraph, no LLM.

    Pipeline:
      1. IMSI discovery pass (Loki query in alarm window)
      2. Per-IMSI trace construction (wider window for full procedure)
      3. Memgraph ingestion + deviation detection against reference DAG
    """
    alert_time = int(parse_timestamp(state["alert"]["startsAt"]))

    # 1. Discovery query — find all active IMSIs in alarm window
    discovery_logql = '{k8s_namespace_name="5g-core"} |~ "(?i)imsi-"'
    discovery_logs = loki_query(
        discovery_logql, start=alert_time - 30, end=alert_time + 30
    )
    imsis = extract_unique_imsis(discovery_logs)

    # 2. Per-IMSI trace construction (wider window: -2min to +60s)
    traces: list[dict[str, Any]] = []
    for imsi in imsis:
        logql = per_imsi_logql(imsi)
        raw_trace = loki_query(
            logql, start=alert_time - 120, end=alert_time + 60
        )
        contracted = contract_imsi_trace(raw_trace, imsi)
        traces.append(contracted)

    # 3. Ingest into Memgraph and run deviation detection
    ingest_traces_to_memgraph(traces, state["incident_id"])

    dag_name = state.get("procedure_name", "")
    deviations = run_deviation_detection(state["incident_id"], dag_name)

    state["discovered_imsis"] = imsis
    state["traces_ready"] = True
    state["trace_deviations"] = deviations

    return state
