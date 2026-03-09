# 5G TriageAgent — Developer Guide

## 1. Overview

5G TriageAgent is a multi-agent LangGraph orchestration system for real-time root cause analysis
of 5G core network failures. When Prometheus Alertmanager fires an alert (e.g.
`RegistrationFailures`), the system runs a directed pipeline of specialized agents to localise the
failure across three layers: **infrastructure** (pod restarts, OOM kills), **network function**
(NF metrics and logs), and **3GPP procedure** (UE trace deviations against reference DAGs).

The pipeline queries five data sources — Kubernetes pod metrics, NF-level Prometheus metrics,
Loki logs, Memgraph reference DAGs, and live UE signalling traces — then sends compressed evidence
to an LLM that produces a structured root cause report: `root_nf`, `failure_mode`, `layer`,
`confidence` (0–1), and a timestamped `evidence_chain`.

All agents except RCAAgent are deterministic (rule-based or query-based). Only RCAAgent calls an LLM.

## 2. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | |
| Docker / Kubernetes | For deployment; local dev uses `uvicorn` directly |
| Prometheus | Reachable at `PROMETHEUS_URL` (default: `http://kube-prom-kube-prometheus-prometheus.monitoring:9090`) |
| Loki | Reachable at `LOKI_URL` (default: `http://loki.monitoring:3100`) |
| Memgraph | Bolt port 7687; runs as a sidecar in production; standalone for local dev |
| `mgconsole` | CLI tool to load DAG Cypher files into Memgraph |
| LLM access | Set `LLM_PROVIDER` + `LLM_API_KEY` (cloud) or `LLM_BASE_URL` (local vLLM/Ollama) |
| LangSmith (optional) | Set `LANGCHAIN_TRACING_V2=true` + `LANGSMITH_API_KEY` for span tracing |

## 3. Quick Start

```bash
# 1. Install
git clone <repo>
cd 5g-triage-agent
pip install -e ".[dev]"

# 2. Start Memgraph (local dev — Docker)
docker run -d -p 7687:7687 memgraph/memgraph:latest

# 3. Load DAGs into Memgraph
mgconsole < dags/registration_general.cypher
mgconsole < dags/authentication_5g_aka.cypher
mgconsole < dags/pdu_session_establishment.cypher

# Verify DAGs loaded
mgconsole -host localhost -port 7687 <<< "MATCH (t:ReferenceTrace) RETURN t.name;"

# 4. Set environment variables (minimum for local dev)
export LLM_PROVIDER=openai
export LLM_API_KEY=sk-...
export PROMETHEUS_URL=http://localhost:9090   # or your cluster URL
export LOKI_URL=http://localhost:3100

# 5. Start the webhook server
uvicorn triage_agent.api.webhook:app --reload --port 8000

# 6. Send a test alert
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "status": "firing",
    "receiver": "triage-agent",
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "RegistrationFailures",
        "severity": "critical",
        "namespace": "5g-core",
        "nf": "amf"
      },
      "annotations": {"summary": "Registration failures detected"},
      "startsAt": "2026-02-15T10:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "",
      "fingerprint": "abc123"
    }]
  }'
# Response: {"incident_id": "abc-123", "status": "accepted", ...}

# 7. Poll for result
curl http://localhost:8000/incidents/<incident_id>
# {"status": "pending"} while running; {"status": "complete", "final_report": {...}} when done
```

## 4. System Architecture

```mermaid
flowchart TD
    A["Alertmanager"] -->|webhook| B["LangGraph Orchestrator"]
    B --> C(["START"])

    C --> D["InfraAgent\nCheck pod metrics via MCP"]
    C --> DM["DagMapper\nAlert → 3GPP procedure DAGs"]

    DM --> E["NfMetricsAgent\nMCP: Prometheus"]
    DM --> E2["NfLogsAgent\nMCP: Loki (+ HTTP fallback)"]
    DM --> E3["UeTracesAgent\nMCP: Loki + Memgraph ingest"]

    E --> F["EvidenceQuality"]
    E2 --> F
    E3 --> F

    D --> JR
    F --> JR["join_for_rca\n(compress evidence)"]
    JR --> G["RCAAgent\nLLM analysis"]

    G --> RETRY{"should_retry?"}
    RETRY -->|"retry"| INC["increment_attempt"]
    INC --> G
    RETRY -->|"finalize"| H["finalize_report"]
    H --> I(["END"])
```

### How the pipeline works

**Parallel start:** `InfraAgent` and `DagMapper` both start immediately from `START`. They are
independent — infra triage does not need to know which 3GPP procedures are involved.

**Evidence fan-out:** Once `DagMapper` writes `nf_union` (the list of NFs involved in the matched
procedures), LangGraph fans out to `NfMetricsAgent`, `NfLogsAgent`, and `UeTracesAgent` in
parallel. All three query different data sources for the same set of NFs over the same time window.

**Evidence convergence:** All three collection agents write to `EvidenceQuality`, which scores
the diversity of evidence collected (0.10–0.95 depending on which sources have data).

**`join_for_rca` barrier:** This node has two incoming edges — from `InfraAgent` and from
`EvidenceQuality`. LangGraph waits for **both** to complete before executing `join_for_rca`.
This guarantees that `infra_findings` and `infra_score` are in state before the LLM prompt is
built. `join_for_rca` compresses all evidence sections to fit within the LLM context budget,
writing the result to `state["compressed_evidence"]`.

**RCAAgent and retry loop:** RCAAgent reads `compressed_evidence` and calls the LLM. If confidence
is below the threshold (0.70 by default, 0.65 if evidence quality ≥ 0.80), `should_retry` routes
to `increment_attempt → rca_agent` for a second pass. Hard limit: `max_attempts=2`. After the
final attempt, `finalize_report` writes `state["final_report"]`.

## 5. Agent Reference

Each agent is a Python function that takes `TriageState` and returns a `dict` delta. LangGraph
merges the delta into the shared state. Source files are in `src/triage_agent/agents/`.

---

### InfraAgent (`agents/infra_agent.py`)

**Reads from state:**
- `alert["labels"]["namespace"]` — K8s namespace for PromQL scoping (default `"5g-core"`)
- `alert["labels"]["nf"]` — optional NF name hint for log correlation
- `incident_id` — for artifact snapshots

**How it works:**
Builds four PromQL queries scoped to the namespace: pod restarts (window: `promql_restart_window=1h`),
OOM kills (window: `promql_oom_window=5m`), pod status (Pending/Failed/Unknown), and CPU usage
(window: `promql_cpu_rate_window_infra=2m`). Queries Prometheus via MCP.

Scores each dimension with configurable weights:
- Restarts: weight 0.35 (breakpoints: >5 restarts → 1.0, ≥3 → 0.7, ≥1 → 0.4)
- OOM kills: weight 0.25 (any OOM → 1.0)
- Pod status: weight 0.20 (Failed/Unknown → 1.0, Pending → 0.6)
- CPU/Memory: weight 0.20 (memory >90% → 1.0, CPU >1 core → 0.8)

`infra_score = sum(weight × factor)` clamped to [0.0, 1.0].

**Writes to state:**
- `infra_checked: bool = True`
- `infra_score: float` — 0.0 (no issue) to 1.0 (confirmed infra failure)
- `infra_findings: dict` — raw pod metrics, events, resource usage

**Consumed by:** `join_for_rca` (passes `infra_score` and `infra_findings` JSON into the RCA prompt).
RCAAgent uses `infra_score` to determine `layer`: ≥0.80 → `"infrastructure"`, ≥0.60 → possible
infra-triggered application failure, <0.30 → `"application"`.

---

### DagMapper (`agents/dag_mapper.py`)

**Reads from state:**
- `alert["labels"]["alertname"]` — primary match signal
- `alert["labels"]["nf"]` — NF hint for `nf_default` tier
- `alert["annotations"]["description"]` — keyword search

**How it works:**
Priority cascade (stops at first match):
1. **exact_match** — `alertname` exactly equals a known DAG name → `mapping_confidence=1.0`
2. **keyword_match** — `alertname`/`description` contains a key from `KEYWORD_MAP`
   (e.g. `"auth"` → `["Authentication_5G_AKA", "Registration_General"]`) → `mapping_confidence=0.8`
3. **nf_default** — `alert["labels"]["nf"]` maps via `NF_DEFAULT_MAP`
   (e.g. `"amf"` → `["Registration_General", "Authentication_5G_AKA"]`) → `mapping_confidence=0.6`
4. **generic_fallback** — all three known DAGs → `mapping_confidence=0.4`

For each matched DAG ID, fetches the full DAG dict from Memgraph via Bolt (Cypher:
`MATCH (t:ReferenceTrace {name: $name})-[:HAS_PHASE]->(e:RefEvent) RETURN t, e ORDER BY e.order`).

**Writes to state:**
- `procedure_names: list[str]` — e.g. `["Registration_General", "Authentication_5G_AKA"]`
- `dag_ids: list[str]` — same values (DAG name = DAG ID in this system)
- `dags: list[dict]` — full DAG dicts with phases, NFs, failure_patterns
- `nf_union: list[str]` — deduplicated union of `all_nfs` across all matched DAGs
- `mapping_confidence: float`
- `mapping_method: str` — `"exact_match"` | `"keyword_match"` | `"nf_default"` | `"generic_fallback"`

**Consumed by:** `NfMetricsAgent`, `NfLogsAgent`, `UeTracesAgent` (all use `nf_union` to scope
their queries). `join_for_rca` includes DAG JSON in the RCA prompt.

---

### NfMetricsAgent (`agents/metrics_agent.py`)

**Reads from state:**
- `nf_union: list[str]` — which NFs to query
- `alert["startsAt"]` — ISO 8601 timestamp; defines the query time window
- `incident_id` — for artifact snapshots

**How it works:**
For each NF in `nf_union`, runs Prometheus range queries:
- HTTP error rate: `rate(http_requests_total{nf=~"...", status=~"5.."}[1m])`
  (window: `promql_error_rate_window=1m`)
- p95 latency: `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{...}[5m]))`
  (quantile: `promql_latency_quantile=0.95`, window: `promql_cpu_rate_window_nf=5m`)
- CPU usage: `rate(container_cpu_usage_seconds_total{container="<nf>"}[5m])`

Time window: `[alert_time − alert_lookback_seconds, alert_time + alert_lookahead_seconds]`
(defaults: 300s before, 60s after). Step: `promql_range_step=15s`.

Compresses result via `compress_nf_metrics()` (budget: `rca_token_budget_metrics=500` tokens ≈ 2000 chars).

**Writes to state:**
- `metrics: dict[str, list[dict]]` — keyed by NF name, each value is a list of metric data points

**Consumed by:** `EvidenceQuality` (presence of metrics → +score), `join_for_rca` (metrics section
of the RCA prompt).

---

### NfLogsAgent (`agents/logs_agent.py`)

**Reads from state:**
- `nf_union: list[str]` — which NFs to query
- `alert["startsAt"]` — defines the log time window
- `dags: list[dict]` — `failure_patterns` used to annotate log entries with matched DAG phases
- `incident_id` — for artifact snapshots

**How it works:**
Builds Loki LogQL queries per NF using namespace and pod label selectors. The time window is the
same `[alert_time − 300s, alert_time + 60s]` window, expressed as nanosecond epoch timestamps.
Max log lines: `loki_query_limit=1000`.

After fetching, annotates each log entry: if the message matches a `failure_pattern` wildcard
from a DAG phase (e.g. `"*auth*fail*"`), the entry gets `{"matched_dag_phase": <phase_id>}`.

Compresses via `compress_nf_logs()`:
- Budget: `rca_token_budget_logs=1300` tokens ≈ 5200 chars
- Per-message truncation: `rca_log_max_message_chars=200` chars

**Writes to state:**
- `logs: dict[str, list[dict]]` — keyed by NF name; each entry has `timestamp`, `message`,
  `level`, `pod`, and optionally `matched_dag_phase`

**Consumed by:** `EvidenceQuality` (presence of logs → +score), `UeTracesAgent` (extracts IMSI
numbers from log messages), `join_for_rca` (logs section of the RCA prompt).

---

### UeTracesAgent (`agents/ue_traces_agent.py`)

**Reads from state:**
- `logs: dict` — scans messages for IMSI numbers (15-digit sequences, configurable via `imsi_digit_length=15`)
- `alert["startsAt"]` — defines the IMSI discovery and trace windows
- `dags: list[dict]` — reference DAG for Cypher comparison
- `incident_id` — for artifact snapshots

**How it works:**

1. **IMSI discovery:** Scans `logs` for IMSI patterns within `imsi_discovery_window_seconds=30`
   of `alert_time`. Deduplicated list written to `discovered_imsis`.

2. **Trace collection:** For each IMSI, queries Loki for all signalling events in the wider window
   `[alert_time − imsi_trace_lookback_seconds, alert_time]` (default: 120s lookback).

3. **Memgraph ingestion:** Creates `(:IMSITrace {imsi, incident_id})` and `(:TraceEvent {order,
   nf, action, timestamp})` nodes. Wires them with `[:HAS_EVENT]` and `[:NEXT_EVENT]` edges.

4. **Deviation detection:** Runs a Cypher query comparing each `(:TraceEvent)` against the
   reference `(:RefEvent)` nodes from the loaded DAG. Deviations include: missing mandatory phases,
   wrong NF for a phase, unexpected action at a step.

Compresses via `compress_trace_deviations()`:
- Budget: `rca_token_budget_traces=500` tokens ≈ 2000 chars
- Max deviations per DAG: `rca_max_deviations_per_dag=3`

**Writes to state:**
- `discovered_imsis: list[str]`
- `traces_ready: bool = True`
- `trace_deviations: dict[str, list[dict]]` — keyed by DAG name; each deviation has
  `deviation_point`, `expected`, `actual`, `expected_nf`, `actual_nf`

**Consumed by:** `EvidenceQuality` (presence of `traces_ready=True` → highest score tier),
`join_for_rca` (trace deviations section of the RCA prompt).

---

### EvidenceQualityAgent (`agents/evidence_quality.py`)

**Reads from state:**
- `metrics` — truthy check
- `logs` — truthy check
- `traces_ready: bool`

**How it works:**
Rule-based scoring based on which sources have data:

| Sources present | Score |
|----------------|-------|
| metrics + logs + traces | 0.95 |
| traces + one other | 0.85 |
| metrics + logs (no traces) | 0.80 |
| traces only | 0.50 |
| metrics only | 0.40 |
| logs only | 0.35 |
| none | 0.10 |

All thresholds are configurable via `eq_score_*` config fields.

**Writes to state:**
- `evidence_quality_score: float`

**Consumed by:** `join_for_rca` (score included in RCA prompt for the LLM's reference).
RCAAgent reads `evidence_quality_score` to select the confidence gate: if score ≥ 0.80
(`high_evidence_threshold`), use relaxed gate (`min_confidence_relaxed=0.65`); else use
`min_confidence_default=0.70`.

---

### join_for_rca (`agents/rca_agent.py`)

**Reads from state:**
- `infra_findings`, `infra_score` — written by InfraAgent
- `metrics`, `logs`, `trace_deviations`, `dags` — written by collection agents
- `evidence_quality_score` — written by EvidenceQuality

**How it works:**
This is an explicit **barrier node** — not an agent, but a synchronisation point. It has two
incoming graph edges (from `infra_agent` and from `evidence_quality`). LangGraph waits for both
to complete before executing this node.

Calls `compress_evidence(state)` which applies per-section token budgets:
- `infra_findings` → serialised as JSON (budget: `rca_token_budget_infra=400` tokens)
- `dags` → `compress_dag(dags, budget=rca_token_budget_dag=800)`
- `metrics` → `format_metrics_for_prompt(metrics)`
- `logs` → `format_logs_for_prompt(logs)`
- `trace_deviations` → `compress_trace_deviations(deviations, budget=rca_token_budget_traces=500)`

Total evidence target: ~3500 tokens, leaving room for the ~400-token prompt template and the
4096-token LLM response.

**Writes to state:**
- `compressed_evidence: dict[str, str]` — 5 keys: `infra_findings_json`, `dag_json`,
  `metrics_formatted`, `logs_formatted`, `trace_deviations_formatted`

**Consumed by:** `RCAAgent` — reads `state["compressed_evidence"]` directly (hard access;
a `KeyError` here means the graph topology is broken).

---

### RCAAgent (`agents/rca_agent.py` → `rca_agent_first_attempt`)

**Reads from state:**
- `compressed_evidence: dict[str, str]` — the 5-section compressed prompt input
- `procedure_names: list[str]` — included in the prompt header
- `infra_score: float` — included with the infra thresholds
- `evidence_quality_score: float` — gating confidence threshold

**How it works:**
Formats `RCA_PROMPT_TEMPLATE` with all compressed evidence sections plus threshold values
(`infra_root_cause_threshold=0.80`, `infra_triggered_threshold=0.60`, `app_only_threshold=0.30`).

Creates LLM via `create_llm()` factory:
- `"openai"` → `ChatOpenAI` with `llm_api_key` + `llm_model`
- `"anthropic"` → `ChatAnthropic` (requires `pip install triage-agent[anthropic]`)
- `"local"` → `ChatOpenAI` with `llm_base_url` (OpenAI-compatible, e.g. vLLM/Ollama)

Default model: `qwen3-4b-instruct-2507.Q4_K_M.gguf` via local vLLM. Temperature: 0.1.
Timeout: `llm_timeout=300s`. On timeout, returns a low-confidence sentinel:
`{root_nf: "unknown", failure_mode: "llm_timeout", confidence: 0.0, needs_more_evidence: False}`.

Parses structured JSON response into `RCAOutput` model. Determines `needs_more_evidence`:
confidence < threshold → `True` (triggers retry) or `False` (triggers finalize).

**Writes to state:**
- `root_nf: str` — name of the root-cause NF (or `"pod-level"` for infra layer)
- `failure_mode: str` — from DAG `failure_patterns` or infra event
- `confidence: float` — 0.0–1.0
- `evidence_chain: list[dict]` — timestamped evidence items with source/nf/type/content/significance
- `layer: str` — `"infrastructure"` or `"application"`
- `needs_more_evidence: bool`
- `evidence_gaps: list[str] | None`
