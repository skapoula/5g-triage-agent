# Developer Documentation Design

**Date:** 2026-03-09
**Status:** Approved
**Audience:** New engineers joining the team (onboarding + contribution)

---

## Goal

Replace the product-focused `triageagent_architecture_design2.md` with developer-focused documentation
that lets a new engineer understand the system, run it locally, contribute to it, and debug it —
without needing to read source code first.

---

## Output Files

| File | Purpose |
|------|---------|
| `docs/README.md` | Hub: architecture, agent reference, DAG, time dimension, state, LangGraph internals, incident lifecycle, debugging |
| `docs/configuration-reference.md` | All ~75 config fields grouped by section with types, defaults, and examples |
| `docs/agent-development.md` | Code templates, `@traceable`, state mutation patterns, MCP usage, testing patterns |
| `docs/triageagent_architecture_design2.archive.md` | Backup copy of old PRD (history only, not linked) |

---

## docs/README.md — Section Design

### 1. Overview
What the system does (one paragraph): Prometheus fires alert → LangGraph pipeline runs → root cause
localised across infra/NF/3GPP procedure layers. Mention the five data sources (infra metrics,
NF metrics, NF logs, UE traces, reference DAGs). State what the output is: `root_nf`, `failure_mode`,
`confidence`, `evidence_chain`.

### 2. Prerequisites
- Python 3.11+
- Docker / Kubernetes
- Prometheus + Loki reachable (URLs in config)
- Memgraph (Bolt port 7687) — sidecar or standalone
- MCP server running (or direct HTTP fallback)
- `LLM_API_KEY` / `LLM_BASE_URL` for RCAAgent
- `LANGSMITH_API_KEY` optional (tracing)

### 3. Quick Start
End-to-end in ~10 commands: clone, `pip install -e ".[dev]"`, set env vars, load DAGs via
`mgconsole < dags/*.cypher`, start uvicorn, send a test webhook, poll for result.
Include exact curl commands and expected JSON shape.

### 4. System Architecture
Mermaid pipeline diagram (already in `docs/workflow_diagram.mermaid`).
Narrative explanation:
- `START` fans out to `InfraAgent` and `DagMapper` in parallel
- `DagMapper` fans out to `NfMetricsAgent`, `NfLogsAgent`, `UeTracesAgent` in parallel
- All three converge at `EvidenceQuality`
- Both `EvidenceQuality` and `InfraAgent` converge at `join_for_rca` (explicit barrier)
- `RCAAgent` runs; conditional edge → retry loop or `finalize` → `END`
- Why parallel: infra check is independent of procedure mapping; NF data collection
  is independent across data sources

### 5. Agent Reference
Per agent: **Reads** (exact state fields) → **How it works** → **Writes** (exact state fields) →
**Consumed by**.

**InfraAgent** (`agents/infra_agent.py`)
- Reads: `alert["labels"]` (namespace, NF name), `incident_id`
- How: Queries Prometheus for pod restarts, OOM kills, pod status, CPU/memory. Scores each dimension
  with configurable weights. Produces a weighted `infra_score`.
- Writes: `infra_checked=True`, `infra_score: float`, `infra_findings: dict`
- Consumed by: `join_for_rca` (passes findings to RCA prompt), `RCAAgent` (uses `infra_score` for layer determination)

**DagMapper** (`agents/dag_mapper.py`)
- Reads: `alert["labels"]["alertname"]`, `alert["labels"]["nf"]`
- How: Matches alert to 3GPP procedure DAGs via exact → keyword → nf_default → generic_fallback.
  Fetches matching DAGs from Memgraph via Bolt.
- Writes: `procedure_names: list[str]`, `dag_ids: list[str]`, `dags: list[dict]`,
  `nf_union: list[str]`, `mapping_confidence: float`, `mapping_method: str`
- Consumed by: `NfMetricsAgent`, `NfLogsAgent`, `UeTracesAgent` (all use `nf_union` to know which
  NFs to query); `join_for_rca` (DAG JSON included in RCA prompt)

**NfMetricsAgent** (`agents/metrics_agent.py`)
- Reads: `nf_union`, `alert["startsAt"]` (→ time window), `incident_id`
- How: For each NF in `nf_union`, queries Prometheus range API for error rates, latency (p95),
  CPU usage, using `alert_lookback_seconds` / `alert_lookahead_seconds` window.
  Compresses result via `compress_nf_metrics` (budget: `rca_token_budget_metrics`).
- Writes: `metrics: dict[str, list[dict]]` (keyed by NF name)
- Consumed by: `EvidenceQuality` (scoring), `join_for_rca` (metrics section of RCA prompt)

**NfLogsAgent** (`agents/logs_agent.py`)
- Reads: `nf_union`, `alert["startsAt"]`, `dags` (failure_patterns for annotation), `incident_id`
- How: Builds Loki LogQL queries per NF using label selectors. Annotates log entries with matched
  DAG phase where a failure_pattern matches. Compresses via `compress_nf_logs`
  (budget: `rca_token_budget_logs`, per-message truncation: `rca_log_max_message_chars`).
- Writes: `logs: dict[str, list[dict]]` (keyed by NF name)
- Consumed by: `EvidenceQuality` (scoring), `UeTracesAgent` (IMSI extraction), `join_for_rca` (logs section of RCA prompt)

**UeTracesAgent** (`agents/ue_traces_agent.py`)
- Reads: `logs` (extracts IMSIs), `alert["startsAt"]`, `dags` (reference DAG for comparison), `incident_id`
- How: Extracts IMSI numbers from log messages within `imsi_discovery_window_seconds` of alert time.
  For each IMSI, queries Loki for the full signalling trace using `imsi_trace_lookback_seconds`.
  Ingests trace events into Memgraph as a live graph. Runs Cypher comparison against the reference
  DAG to detect deviations. Compresses via `compress_trace_deviations`
  (budget: `rca_token_budget_traces`, max deviations: `rca_max_deviations_per_dag`).
- Writes: `discovered_imsis: list[str]`, `traces_ready: bool`, `trace_deviations: dict[str, list[dict]]`
- Consumed by: `EvidenceQuality` (scoring), `join_for_rca` (trace deviations section of RCA prompt)

**EvidenceQualityAgent** (`agents/evidence_quality.py`)
- Reads: `metrics`, `logs`, `trace_deviations`
- How: Rule-based scoring: all three sources → 0.95; traces + one other → 0.85;
  metrics + logs → 0.80; traces only → 0.50; metrics only → 0.40; logs only → 0.35; none → 0.10.
- Writes: `evidence_quality_score: float`
- Consumed by: `join_for_rca` (included in RCA prompt), `RCAAgent` (gates confidence threshold:
  high quality → relaxed gate `min_confidence_relaxed`, else `min_confidence_default`)

**join_for_rca** (`agents/rca_agent.py`)
- Reads: all evidence already written to state (infra_findings, metrics, logs, trace_deviations, dags)
- How: Explicit LangGraph barrier node — waits for both `infra_agent` and `evidence_quality` to
  complete before running. Calls `compress_evidence()` which applies per-section token budgets
  via `compress_dag` and `compress_trace_deviations` (in `utils.py`). Builds the 5 prompt sections.
- Writes: `compressed_evidence: dict[str, str]` (5 keys: infra_findings_json, dag_json,
  metrics_formatted, logs_formatted, trace_deviations_formatted)
- Consumed by: `RCAAgent` (reads `compressed_evidence` directly — hard access, not fallback)

**RCAAgent** (`agents/rca_agent.py` → `rca_agent_first_attempt`)
- Reads: `compressed_evidence`, `procedure_names`, `infra_score`, `evidence_quality_score`, `incident_id`
- How: Formats `RCA_PROMPT_TEMPLATE` with compressed sections and threshold values. Calls LLM
  (openai / anthropic / local via `create_llm` factory). Parses structured JSON response into
  `RCAOutput` model. If LLM times out, returns low-confidence sentinel
  (`root_nf="unknown"`, `failure_mode="llm_timeout"`, `confidence=0.0`). Determines
  `needs_more_evidence` based on confidence vs threshold gate.
- Writes: `root_nf`, `failure_mode`, `confidence`, `evidence_chain`, `layer`, `needs_more_evidence`,
  `evidence_gaps`
- Consumed by: conditional edge → retry loop (second attempt) or `finalize` → `END`

### 6. DAG Reference

**DAG Construction — What You Must Run**
DAG definitions are Cypher scripts in `dags/`. They are loaded into Memgraph by an init container
that runs before the main application starts. To load manually:
```bash
mgconsole < dags/registration_general.cypher
mgconsole < dags/authentication_5g_aka.cypher
mgconsole < dags/pdu_session_establishment.cypher
```
A `.cypher` file creates `(:Procedure)` and `(:Phase)` nodes with `[:HAS_PHASE]` and
`[:NEXT_PHASE]` edges. Each phase carries: `order`, `nf`, `action`, `keywords[]`,
`failure_patterns[]`, `optional`, and optionally `sub_dag` (reference to another procedure).

**DAG Structure**
A compiled DAG dict (as stored in `state["dags"]`) has:
- `name`: identifier (e.g. `registration_general`)
- `spec`: 3GPP spec reference (e.g. `TS 23.502 4.2.2.2.2`)
- `procedure`: human name
- `all_nfs`: deduplicated list of NFs involved (used as `nf_union`)
- `phases[]`: ordered list of phase objects with `failure_patterns` for log matching

**Mapping Strategy** (`DagMapper`)
1. Exact match: `alertname` maps directly to a known DAG name
2. Keyword match: alert labels / annotations contain procedure keywords
3. NF default: alert's NF label → default procedure for that NF
4. Generic fallback: `generic_5g_procedure` DAG (catches anything)

`mapping_confidence` reflects which tier matched (1.0 → 0.4).

**Trace Ingestion at Runtime**
`UeTracesAgent` ingests live IMSI traces into Memgraph during each investigation:
creates `(:IMSITrace)` and `(:TraceEvent)` nodes, runs a Cypher comparison query against
the reference `(:Phase)` nodes to find deviations (missing phases, wrong NF, unexpected order).
These are stored as `trace_deviations: dict[dag_name → list[deviation_dict]]`.

**compress_dag**
Before the RCA prompt is built, `compress_dag(dags, budget=rca_token_budget_dag)` strips
phases to their core fields and truncates if total character count exceeds the budget
(1 token ≈ 4 chars, budget default: 800 tokens → 3200 chars).

### 7. Time Dimension

**Evidence window** — The shared temporal frame for all evidence collection:
- `alert_time = alert["startsAt"]` parsed to Unix timestamp
- Window: `[alert_time − alert_lookback_seconds, alert_time + alert_lookahead_seconds]`
- Defaults: `alert_lookback_seconds=300` (5 min before) + `alert_lookahead_seconds=60` (1 min after)
- This window is applied independently by InfraAgent, NfMetricsAgent, NfLogsAgent

**Prometheus range queries** (`NfMetricsAgent`)
- Translates window to `start` / `end` Unix timestamps for the Prometheus `/query_range` API
- Resolution step: `promql_range_step=15s`

**Loki queries** (`NfLogsAgent`)
- Translates window to nanosecond epoch timestamps for the Loki `/query_range` API
- Max log lines per query: `loki_query_limit=1000`

**IMSI time windows** (`UeTracesAgent`)
Two distinct windows:
- **Discovery window**: `imsi_discovery_window_seconds=30` (narrow, around alert_time) — finds which
  IMSIs were active at the moment of failure
- **Trace window**: `imsi_trace_lookback_seconds=120` (wider lookback per IMSI) — captures the full
  signalling procedure that led to the failure

**Temporal precedence in RCA** (`RCAAgent`)
The LLM prompt instructs the model to use temporal ordering when choosing root cause:
the earliest anomaly in the evidence window is more likely the root cause than a later symptom.
Each item in `evidence_chain` carries a `timestamp` field so the model can reason about
event ordering. The `failed_phase` output field identifies where in the DAG the failure
first manifested.

**Incident TTL** (`api/webhook.py`)
- Completed incident entries expire after `incident_ttl_seconds=3600` (1 hour)
- `_evict_stale()` is called on every incoming POST — no background thread required
- Prevents unbounded memory growth in long-running deployments

**LangSmith trace timestamps**
Every `@traceable`-decorated agent function emits a span with wall-clock start/end times.
When debugging, the LangSmith UI shows span timestamps relative to each other, letting you
verify that the `join_for_rca` barrier ran after both `infra_agent` and `evidence_quality`.

### 8. State Fields
Full table: field | type | written by | read by | notes.
All fields from `src/triage_agent/state.py` (~45 total).

### 9. LangGraph Internals
- How `graph.py` registers each agent as a node (`workflow.add_node`)
- How parallel fan-out works (multiple `add_edge` calls from one source node)
- How `join_for_rca` enforces the infra barrier (both `infra_agent` and `evidence_quality` edges
  point to it; LangGraph waits for all incoming edges before running the node)
- The conditional edge: `rca_agent → should_retry` function → `rca_agent` (retry) or `finalize`
- How to add a new node: write agent function, register node, wire edges, update `TriageState`

### 10. Incident Lifecycle
1. `POST /webhook` → validate Alertmanager payload → generate `incident_id` → enqueue background task
2. Background task calls `workflow.invoke(get_initial_state(alert))`
3. Polling: `GET /incidents/{incident_id}` → 202 while running, 200 with final report when done
4. Final report shape: `{incident_id, root_nf, failure_mode, layer, confidence, evidence_chain, ...}`
5. TTL eviction after `incident_ttl_seconds`

### 11. Debugging
- **LangSmith**: set `LANGCHAIN_TRACING_V2=true` + `LANGSMITH_API_KEY` → full span tree per run
- **Replay a run locally**: `get_initial_state(alert)` + `workflow.invoke()` in a Python REPL
- **`compressed_evidence=None` error**: means `join_for_rca` did not run before `rca_agent` —
  check graph topology in `graph.py`
- **LLM timeout sentinel**: `confidence=0.0`, `failure_mode="llm_timeout"` — raise `llm_timeout` or
  switch provider
- **Empty `dags`**: Memgraph DAGs not loaded — run `mgconsole < dags/*.cypher`
- **`mapping_method=generic_fallback`**: alert not matching any procedure — add keyword mapping
  or extend `DagMapper`

---

## docs/configuration-reference.md — Section Design

All fields from `TriageAgentConfig` (currently ~75 fields), grouped by the config section
comments in `config.py`:

1. **Database and Cluster Infrastructure** — Memgraph host/port/pool/retries, known_nfs, core_namespace
2. **Service Connectivity and Timeouts** — Prometheus/Loki URLs, MCP timeout, retries, CORS, incident TTL, server host/port
3. **LLM / Model Parameters** — provider, model, api_key, base_url, timeout, temperature
4. **Pipeline Flow / Retry Logic** — max_attempts, confidence gates, evidence thresholds, time windows, IMSI windows
5. **PromQL / LogQL Parameters** — all promql_* and loki_* fields
6. **Scoring Thresholds and Weights** — infra weights, restart/resource thresholds, RCA layer thresholds, compression budgets, evidence quality scores
7. **Observability** — LangSmith fields, artifacts_dir, latency threshold, app_version

For each field: name | env var | type | default | description | example override.

---

## docs/agent-development.md — Section Design

1. **Agent contract** — a node function takes `TriageState`, returns `dict[str, Any]` delta (LangGraph merges it)
2. **Minimal template** — 15-line skeleton with `@traceable`, type hints, `get_config()` call
3. **Reading state** — use `state.get("field")` with None guards; never assume upstream ran
4. **Writing state** — return only the fields you own; never return the full state
5. **MCP usage** — how to get the `MCPClient` instance, `await client.query_prometheus()`, fallback pattern
6. **Memgraph usage** — `MemgraphConnection`, `execute_cypher()`, retry semantics
7. **Adding a new node to the graph** — 4 steps: write agent, register node, wire edges, add fields to `TriageState`
8. **Testing patterns** — fixture usage (`sample_initial_state`), mocking MCP/Memgraph, asserting state delta shape
9. **Common mistakes** — no LLM calls outside `rca_agent.py`, no blocking I/O in async, always `@traceable`

---

## Backup

`docs/triageagent_architecture_design2.md` → copied to `docs/triageagent_architecture_design2.archive.md`
(not linked from anywhere; history only).
