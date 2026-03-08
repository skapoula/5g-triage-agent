"""Shared utility functions for TriageAgent pipeline."""

import concurrent.futures
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp from alert payload. Returns Unix epoch seconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def extract_log_level(message: str) -> str:
    """Extract log level from message text."""
    message_upper = message.upper()
    for level in ("FATAL", "ERROR", "WARN", "INFO", "DEBUG"):
        if level in message_upper:
            return level
    return "INFO"


def parse_loki_response(data: dict[str, Any]) -> list[dict[str, Any]]:
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
                "level": extract_log_level(value[1]),
            })
    return logs


def count_tokens(text: str) -> int:
    """Approximate token count using 4-chars-per-token heuristic."""
    return max(1, len(text) // 4)


def _write_artifact_sync(
    incident_id: str, name: str, data: Any, artifacts_dir: str
) -> None:
    """Write artifact to disk synchronously. Called from a background thread."""
    try:
        target_dir = Path(artifacts_dir) / incident_id
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Failed to save artifact %s/%s: %s", incident_id, name, exc)


def save_artifact(
    incident_id: str, name: str, data: Any, artifacts_dir: str
) -> None:
    """Fire-and-forget artifact write. Non-blocking, non-fatal.

    Spawns a one-shot thread so the calling agent is never delayed by disk I/O.
    Failures are logged as warnings and silently swallowed.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    executor.submit(_write_artifact_sync, incident_id, name, data, artifacts_dir)
    executor.shutdown(wait=False)
