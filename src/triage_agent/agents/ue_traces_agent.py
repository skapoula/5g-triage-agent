"""UeTracesAgent: IMSI trace discovery, construction, and Memgraph ingestion.

No LLM. Discovers active IMSIs in the alarm window via Loki, constructs
per-IMSI traces, ingests them into Memgraph, and runs deviation detection
against reference DAGs.

Pipeline:
  1. IMSI discovery pass (Loki query)
  2. Per-IMSI trace construction
  3. Memgraph ingestion + comparison against reference DAG
"""

from triage_agent.state import TriageState


def loki_query(logql: str, start: int, end: int) -> list:
    """Execute a Loki query via MCP."""
    raise NotImplementedError


def extract_unique_imsis(logs: list) -> list[str]:
    raise NotImplementedError


def per_imsi_logql(imsi: str) -> str:
    raise NotImplementedError


def contract_imsi_trace(raw_trace: list, imsi: str) -> dict:
    raise NotImplementedError


def ingest_traces_to_memgraph(traces: list, incident_id: str) -> None:
    """Ingest per-IMSI traces into Memgraph as :Trace nodes."""
    raise NotImplementedError


def run_deviation_detection(incident_id: str, dag_name: str) -> list[dict]:
    """Compare ingested traces against reference DAG in Memgraph. Returns deviations."""
    raise NotImplementedError


def discover_and_trace_imsis(state: TriageState) -> TriageState:
    """UeTracesAgent entry point. Pure MCP query + Memgraph, no LLM."""
    alert_time = 0  # TODO: parse from state["alert"]["startsAt"]

    # 1. Discovery query (extract unique IMSIs)
    discovery_logql = ""  # TODO: build LogQL for IMSI discovery
    discovery_logs = loki_query(discovery_logql, start=alert_time - 30, end=alert_time + 30)
    imsis = extract_unique_imsis(discovery_logs)

    # 2. Parallel trace construction
    traces = []
    for imsi in imsis:
        logql = per_imsi_logql(imsi)
        raw_trace = loki_query(logql, start=alert_time - 120, end=alert_time + 60)
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
