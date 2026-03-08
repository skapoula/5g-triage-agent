# Design: Parallel Agent Execution with Multi-Procedure DAG Mapping

**Date:** 2026-03-07
**Status:** Approved
**Scope:** `graph.py`, `state.py`, new `dag_mapper.py`, `metrics_agent.py`, `logs_agent.py`, `ue_traces_agent.py`, `evidence_quality.py`

---

## Problem

The current data collection pipeline runs sequentially:

```
START → metrics_agent → logs_agent → traces_agent → evidence_quality
```

The three agents have no data dependencies on each other — `metrics_agent` queries Prometheus, `logs_agent` queries Loki, and `ue_traces_agent` queries Loki + Memgraph independently. Running them sequentially adds unnecessary latency and contradicts the intended architecture described in `CLAUDE.md`.

Additionally, there is no DAG mapping node in the graph. `state["dag"]` is initialised to `None`, causing all three agents to short-circuit with empty results on every invocation. A mapping step that resolves the alert to one or more 3GPP procedure DAGs is missing.

A third issue: an alert can match **more than one** 3GPP procedure (e.g., an AMF failure affects both Registration and Authentication). The current single-`dag` state shape cannot represent this.

---

## Goals

1. Run `NfMetricsAgent`, `NfLogsAgent`, and `UeTracesAgent` in parallel after DAG resolution.
2. Introduce a dedicated `dag_mapper` node that maps an alert to one or more 3GPP procedures and fetches their DAGs from Memgraph.
3. Support multiple matched procedures: collect data once using the union of NFs, run per-procedure deviation detection in Memgraph.
4. Keep all agents deterministic and LangGraph parallel-merge safe (return delta dicts, not full state).

---

## Architecture

### Proposed Graph Topology

```
START → infra_agent ──────────────────────────────────────────────────────────────┐
START → dag_mapper ──┬──→ metrics_agent ──┐                                       │
                     ├──→ logs_agent ─────┼──→ evidence_quality → rca_agent ──────┘
                     └──→ traces_agent ───┘
```

- `infra_agent` and `dag_mapper` both fan out from `START` and run in parallel with each other.
- `dag_mapper` writes the procedure list and DAGs to state, then fans out to all three collection agents simultaneously via three edges.
- All three collection agents converge at `evidence_quality`. LangGraph waits for all three to complete before proceeding.
- `rca_agent` waits for both `infra_agent` and `evidence_quality`.

### Comparison with Current Topology

| | Current | Proposed |
|---|---|---|
| `metrics_agent` start | `START` | `dag_mapper` |
| `logs_agent` start | `metrics_agent` (sequential) | `dag_mapper` (parallel) |
| `traces_agent` start | `logs_agent` (sequential) | `dag_mapper` (parallel) |
| DAG resolution | missing | `dag_mapper` node |
| Multi-procedure support | no | yes |

---

## State Changes (`state.py`)

Three existing fields change shape to support multiple matched procedures:

| Field | Before | After |
|-------|--------|-------|
| `procedure_name` | `str \| None` | `list[str] \| None` |
| `dag_id` | `str \| None` | `list[str] \| None` |
| `dag` | `dict \| None` | `list[dict] \| None` |

One new field is added:

```python
nf_union: list[str] | None  # Deduplicated union of all_nfs across all matched DAGs
```

`mapping_confidence` and `mapping_method` remain as scalars — they reflect the overall alert-to-procedure mapping quality, not per-procedure values.

---

## New Node: `dag_mapper` (`agents/dag_mapper.py`)

A deterministic agent (no LLM) that runs from `START` in parallel with `infra_agent`.

### Responsibilities

1. Read alert labels: `alertname`, `nf`, `pod`, `procedure`.
2. Map alert to one or more 3GPP procedure names using a priority cascade:
   - `exact_match` — alert label `procedure` directly names a known procedure
   - `keyword_match` — alert name/description contains procedure keywords (e.g., "auth", "registration", "pdu")
   - `nf_default` — known NF maps to its default procedures (e.g., AUSF → authentication; SMF → pdu_session_establishment)
   - `generic_fallback` — all known procedures returned with low confidence
3. Fetch each matched procedure's DAG from Memgraph using its `dag_id`.
4. Compute `nf_union` as the deduplicated union of `all_nfs` across all fetched DAGs.
5. Return delta dict:

```python
{
    "procedure_names": list[str],
    "dag_ids": list[str],
    "dags": list[dict],
    "nf_union": list[str],
    "mapping_confidence": float,
    "mapping_method": str,
}
```

### Graceful Degradation

If Memgraph is unreachable or returns no DAGs:
- Returns `dags=[]`, `nf_union=[]`
- Downstream agents detect empty inputs and return empty results
- `evidence_quality` scores low → `rca_agent` runs in degraded mode
- No crash, full observability via `@traceable`

---

## Changes to Existing Agents

### `metrics_agent.py`

- Replace `dag["all_nfs"]` with `state["nf_union"]`.
- Guard: if `nf_union` is empty, return `{"metrics": {}}`.
- Return signature unchanged — already returns delta dict `{"metrics": ...}`. LangGraph parallel-safe.

### `logs_agent.py`

- Replace `dag["all_nfs"]` / `dag["phases"]` with the union of NFs and phases across `state["dags"]`.
- **Fix return type**: currently mutates state and returns the full `TriageState`. Must be changed to return a delta dict:

```python
return {"logs": organize_and_annotate_logs(logs_raw, combined_dag)}
```

- Guard: if `dags` is empty, return `{"logs": {}}`.

### `ue_traces_agent.py`

- Run Memgraph deviation detection against **each DAG** in `state["dags"]` separately, keying results by `procedure_name`:

```python
trace_deviations = {
    "registration": [...],
    "authentication_5g_aka": [...],
}
```

- **Fix return type**: currently mutates state and returns the full `TriageState`. Must be changed to return a delta dict:

```python
return {
    "discovered_imsis": imsis,
    "traces_ready": True,
    "trace_deviations": trace_deviations,
}
```

- Guard: if `dags` is empty, return `{"discovered_imsis": [], "traces_ready": False, "trace_deviations": {}}`.

### `evidence_quality.py`

- No logic change — reads `metrics`, `logs`, `traces_ready` as before.
- **Fix return type**: return delta dict `{"evidence_quality_score": score}` instead of full state for consistency.

### `graph.py`

Remove sequential edges:

```python
# Remove these
workflow.add_edge("metrics_agent", "logs_agent")
workflow.add_edge("logs_agent", "traces_agent")
workflow.add_edge("traces_agent", "evidence_quality")
```

Add new node and parallel edges:

```python
workflow.add_node("dag_mapper", dag_mapper)

workflow.add_edge(START, "dag_mapper")
workflow.add_edge("dag_mapper", "metrics_agent")
workflow.add_edge("dag_mapper", "logs_agent")
workflow.add_edge("dag_mapper", "traces_agent")
workflow.add_edge("metrics_agent", "evidence_quality")
workflow.add_edge("logs_agent", "evidence_quality")
workflow.add_edge("traces_agent", "evidence_quality")
```

The `infra_agent → rca_agent` and `evidence_quality → rca_agent` edges are unchanged.

---

## Error Handling

| Failure scenario | Behaviour |
|---|---|
| Memgraph unreachable in `dag_mapper` | `dags=[]`, all collection agents return empty, degraded RCA |
| Alert matches no known procedure | `generic_fallback` returns all procedures with low confidence |
| Single collection agent fails | Other two complete normally, `evidence_quality` scores partial evidence |
| Memgraph timeout in `ue_traces_agent` | `traces_ready=False`, deviation detection skipped, partial score |
| Prometheus unreachable in `metrics_agent` | `metrics={}`, graceful degradation already implemented |
| Loki unreachable in `logs_agent` | Falls back to direct HTTP, already implemented |

---

## Testing Plan

Per CLAUDE.md, all tests must be written before implementation.

| Test file | Coverage |
|---|---|
| `tests/unit/test_dag_mapper.py` | Alert→procedure mapping cascade (all four methods), multi-procedure output, Memgraph failure degradation, `nf_union` computation |
| `tests/unit/test_metrics_agent.py` | Update fixture: replace `dag` with `nf_union` |
| `tests/unit/test_logs_agent.py` | Multi-dag phase union for query building, delta return dict |
| `tests/unit/test_ue_traces_agent.py` | Per-procedure deviation detection, keyed by procedure name, delta return dict |
| `tests/unit/test_evidence_quality.py` | Delta return dict |
| `tests/unit/test_graph.py` | Verify `dag_mapper` fans out to all three agents; verify all three converge at `evidence_quality` |

---

## Files Touched

```
src/triage_agent/state.py                      # Field shape changes
src/triage_agent/agents/dag_mapper.py          # New file
src/triage_agent/agents/metrics_agent.py       # nf_union input
src/triage_agent/agents/logs_agent.py          # Multi-dag phases + delta return
src/triage_agent/agents/ue_traces_agent.py     # Per-procedure deviation + delta return
src/triage_agent/agents/evidence_quality.py    # Delta return
src/triage_agent/graph.py                      # Rewire edges
tests/unit/test_dag_mapper.py                  # New file
tests/unit/test_metrics_agent.py               # Update fixtures
tests/unit/test_logs_agent.py                  # Update for new behaviour
tests/unit/test_ue_traces_agent.py             # Update for new behaviour
tests/unit/test_evidence_quality.py            # Update for delta return
tests/unit/test_graph.py                       # New topology assertions
```
