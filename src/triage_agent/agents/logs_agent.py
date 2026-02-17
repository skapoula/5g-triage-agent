"""NfLogsAgent: Per-NF log collection from Loki via MCP.

No LLM. Queries Loki for ERROR/WARN/FATAL logs and phase-specific patterns
from the candidate NF list provided by the DAG.

Two-path architecture:
  1. Upfront health check — probe MCP server /ready endpoint.
  2. If MCP reachable → fetch all logs via MCP client.
  3. If MCP unreachable → fetch all logs via direct Loki HTTP API.
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
from triage_agent.state import TriageState

logger = logging.getLogger(__name__)


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp from alert payload. Returns Unix epoch seconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(
        tzinfo=UTC if dt.tzinfo is None else dt.tzinfo
    ).timestamp()


def extract_nf_from_pod_name(pod: str) -> str:
    """Extract NF name prefix from a k8s pod name. Returns lowercase."""
    return pod.split("-")[0].lower()


def wildcard_match(text: str, pattern: str) -> bool:
    """Case-insensitive wildcard matching. '*' matches any characters."""
    regex_pattern = pattern.replace("*", ".*")
    return bool(re.search(f"(?i){regex_pattern}", text))


def build_loki_queries(dag: dict[str, Any], core_namespace: str) -> list[str]:
    """Build LogQL queries for each NF: base ERROR/WARN/FATAL + phase-specific.

    Args:
        dag: DAG dict with 'all_nfs' and 'phases'.
        core_namespace: K8s namespace where 5G core NF pods run.

    Returns:
        List of LogQL query strings.
    """
    queries: list[str] = []
    for nf in dag["all_nfs"]:
        nf_lower = nf.lower()

        # Base query: all ERROR/WARN/FATAL logs
        queries.append(
            f'{{k8s_namespace_name="{core_namespace}",k8s_pod_name=~".*{nf_lower}.*"}} |~ "ERROR|WARN|FATAL"'
        )

        # Phase-specific pattern queries
        for phase in dag["phases"]:
            if nf in phase["actors"]:
                queries.append(
                    f'{{k8s_namespace_name="{core_namespace}",k8s_pod_name=~".*{nf_lower}.*"}} |~ "{phase["success_log"]}"'
                )
                for pattern in phase.get("failure_patterns", []):
                    loki_pattern = pattern.replace("*", ".*")
                    queries.append(
                        f'{{k8s_namespace_name="{core_namespace}",k8s_pod_name=~".*{nf_lower}.*"}} |~ "(?i){loki_pattern}"'
                    )

    return queries


def organize_and_annotate_logs(logs: list[dict[str, Any]], dag: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Organize logs by NF and annotate with matched phase/pattern."""
    organized: dict[str, list[dict[str, Any]]] = {}

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


# --- Loki response parsing (shared by direct-HTTP fallback) ---


def _extract_log_level(message: str) -> str:
    """Extract log level from message text."""
    message_upper = message.upper()
    for level in ("FATAL", "ERROR", "WARN", "INFO", "DEBUG"):
        if level in message_upper:
            return level
    return "INFO"


def _parse_loki_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Loki query_range JSON response into flat log entry list.

    Mirrors MCPClient.query_loki response parsing so that both MCP and
    direct-HTTP paths produce identical output shape.
    """
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


# --- MCP health check ---


async def _check_mcp_available() -> bool:
    """Lightweight MCP health check: probe Loki /ready via MCP server.

    Returns True if MCP server is reachable and Loki reports ready.
    Returns False on any error (connection refused, timeout, etc.).
    """
    try:
        async with MCPClient() as client:
            return await client.health_check_loki()
    except Exception:
        return False


# --- Two fetch paths: MCP and direct Loki HTTP ---


async def _fetch_loki_logs(
    queries: list[str],
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    """Fetch logs from Loki via MCP client.

    Args:
        queries: LogQL query strings to execute.
        start: Unix epoch seconds for window start.
        end: Unix epoch seconds for window end.

    Returns:
        Flat list of log entries across all successful queries.

    Raises:
        Exception: Any MCP/connection/timeout failure.
    """
    if not queries:
        return []

    results: list[dict[str, Any]] = []
    async with MCPClient() as client:
        for query in queries:
            logs = await client.query_loki(query, start=start, end=end)
            results.extend(logs)

    # Normalize pod field: MCPClient reads labels["pod"] which may be empty
    # when Loki uses k8s-style labels (k8s_pod_name).
    for entry in results:
        if not entry.get("pod"):
            entry["pod"] = entry.get("labels", {}).get("k8s_pod_name", "")

    return results


async def _fetch_loki_logs_direct(
    queries: list[str],
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    """Fetch logs directly from Loki HTTP API.

    Queries Loki's query_range endpoint via httpx, bypassing the MCP server.
    Uses the same response parsing as MCPClient to produce identical output.

    Args:
        queries: LogQL query strings to execute.
        start: Unix epoch seconds for window start.
        end: Unix epoch seconds for window end.

    Returns:
        Flat list of log entries across all successful queries.
    """
    if not queries:
        return []

    config = get_config()
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=config.mcp_timeout) as client:
        for query in queries:
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
                results.extend(_parse_loki_response(response.json()))
            except httpx.TimeoutException:
                logger.warning("Loki direct query timed out: %s", query)
            except httpx.HTTPStatusError as exc:
                logger.warning("Loki direct query HTTP error: %s — %s", query, exc)
            except Exception:
                logger.warning("Loki direct query failed: %s", query, exc_info=True)

    return results


# --- Agent entry point ---


@traceable(name="NfLogsAgent")
def logs_agent(state: TriageState) -> TriageState:
    """NfLogsAgent entry point. Pure MCP/HTTP query, no LLM.

    Two-path architecture:
      1. Probe MCP server availability (lightweight /ready check).
      2. If reachable → MCP path for all queries.
      3. If unreachable → direct Loki HTTP path for all queries.
    """
    dag = state["dag"]
    assert dag is not None, "logs_agent requires DAG in state"
    alert_time = parse_timestamp(state["alert"]["startsAt"])
    start = int(alert_time - 300)
    end = int(alert_time + 60)

    queries = build_loki_queries(dag, get_config().core_namespace)

    logs_raw: list[dict[str, Any]] = []
    if queries:
        # Step 1: Determine which path to use
        try:
            use_mcp = asyncio.run(_check_mcp_available())
        except Exception:
            logger.warning(
                "MCP health check failed, defaulting to direct Loki",
                exc_info=True,
            )
            use_mcp = False

        # Step 2: Execute queries on the chosen path
        if use_mcp:
            try:
                logs_raw = asyncio.run(
                    _fetch_loki_logs(queries, start=start, end=end)
                )
            except Exception:
                logger.warning(
                    "MCP queries failed despite passing health check,"
                    " proceeding with empty logs",
                    exc_info=True,
                )
        else:
            logger.info("MCP server unavailable, using direct Loki connection")
            try:
                logs_raw = asyncio.run(
                    _fetch_loki_logs_direct(queries, start=start, end=end)
                )
            except Exception:
                logger.warning(
                    "Direct Loki query failed, proceeding with empty logs",
                    exc_info=True,
                )

    state["logs"] = organize_and_annotate_logs(logs_raw, dag)
    return state
