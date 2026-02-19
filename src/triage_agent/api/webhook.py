"""FastAPI webhook endpoint for Alertmanager."""
# ruff: noqa: N815 — camelCase field names match Alertmanager webhook JSON schema

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from triage_agent.graph import create_workflow, get_initial_state

logger = logging.getLogger(__name__)

app = FastAPI(
    title="5G TriageAgent",
    description="Multi-Agent RCA System for 5G Core Network Failures",
    version="3.2.0",
)

# CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compile the workflow once at startup rather than per request
_workflow = create_workflow()


async def _run_triage(alert_dict: dict[str, Any], incident_id: str) -> None:
    """Run the LangGraph triage workflow in a background thread.

    Uses asyncio.to_thread to avoid nested event loop conflicts since the
    agent functions call asyncio.run() internally.
    """
    try:
        initial_state = get_initial_state(alert=alert_dict, incident_id=incident_id)
        result = await asyncio.to_thread(_workflow.invoke, initial_state)
        logger.info(
            f"Triage complete: incident_id={incident_id}, "
            f"report={result.get('final_report')}"
        )
    except Exception:
        logger.exception(f"Triage failed: incident_id={incident_id}")


class AlertLabel(BaseModel):
    """Alertmanager alert labels."""

    alertname: str
    severity: str = "warning"
    namespace: str = "5g-core"
    nf: str | None = None


class AlertAnnotation(BaseModel):
    """Alertmanager alert annotations."""

    summary: str = ""
    description: str = ""


class Alert(BaseModel):
    """Single alert from Alertmanager."""

    status: str
    labels: AlertLabel
    annotations: AlertAnnotation = AlertAnnotation()
    startsAt: str
    endsAt: str = "0001-01-01T00:00:00Z"
    generatorURL: str = ""
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    """Alertmanager webhook payload."""

    receiver: str = "triage-agent"
    status: str
    alerts: list[Alert]
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: str = ""
    version: str = "4"
    groupKey: str = ""


class TriageResponse(BaseModel):
    """Response from triage endpoint."""

    incident_id: str
    status: str
    message: str
    alerts_received: int


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    timestamp: str
    memgraph: bool
    prometheus: bool
    loki: bool


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint for Kubernetes probes."""
    from triage_agent.mcp.client import MCPClient
    from triage_agent.memgraph.connection import get_memgraph

    # Check Memgraph
    try:
        memgraph = get_memgraph()
        memgraph_ok = memgraph.health_check()
    except Exception:
        memgraph_ok = False

    # Check MCP servers
    async with MCPClient() as mcp:
        prometheus_ok = await mcp.health_check_prometheus()
        loki_ok = await mcp.health_check_loki()

    overall_status = "healthy" if (memgraph_ok and prometheus_ok) else "degraded"

    return HealthResponse(
        status=overall_status,
        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        memgraph=memgraph_ok,
        prometheus=prometheus_ok,
        loki=loki_ok,
    )


@app.post("/webhook", response_model=TriageResponse)
async def receive_alert(
    payload: AlertmanagerPayload, background_tasks: BackgroundTasks
) -> TriageResponse:
    """Receive alerts from Alertmanager and trigger triage workflow."""
    incident_id = str(uuid.uuid4())

    logger.info(
        f"Received {len(payload.alerts)} alerts, incident_id={incident_id}, "
        f"status={payload.status}"
    )

    if not payload.alerts:
        raise HTTPException(status_code=400, detail="No alerts in payload")

    # Only process firing alerts
    firing_alerts = [a for a in payload.alerts if a.status == "firing"]
    if not firing_alerts:
        return TriageResponse(
            incident_id=incident_id,
            status="skipped",
            message="No firing alerts to process",
            alerts_received=len(payload.alerts),
        )

    background_tasks.add_task(_run_triage, firing_alerts[0].model_dump(), incident_id)

    return TriageResponse(
        incident_id=incident_id,
        status="accepted",
        message=f"Processing {len(firing_alerts)} firing alerts",
        alerts_received=len(payload.alerts),
    )


@app.get("/")
async def root() -> dict[str, Any]:
    """Root endpoint with API info."""
    return {
        "name": "5G TriageAgent",
        "version": "3.2.0",
        "docs": "/docs",
        "health": "/health",
        "webhook": "/webhook",
    }
