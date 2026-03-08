# Fix State Inconsistencies Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 13 bugs and inconsistencies found by state-diagram analysis of the 5G TriageAgent pipeline — covering type mismatches, missing keys, dead code, schema mismatches, and architectural flaws.

**Architecture:** All fixes are surgical: correct the wrong key/type/schema at the source, extract shared utilities, and fix the LangGraph join semantics. No new features, no refactors beyond what is required to fix each bug.

**Tech Stack:** Python 3.13, LangGraph, FastAPI, Pydantic, neo4j driver (Memgraph), pytest, mypy --strict, ruff

---

## Pre-flight

Before starting any task:
```bash
cd /home/agent/workspace/5g-triage-agent
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/unit/ -v        # establish baseline
mypy src/ --strict           # establish baseline type errors
```

---

### Task 1: Extract shared utilities (`parse_timestamp`, `_extract_log_level`, `_parse_loki_response`)

**Problem:** Three/four agents each define identical `parse_timestamp`, `_extract_log_level`, and `_parse_loki_response` functions. DRY violation and future maintenance hazard.

**Files:**
- Create: `src/triage_agent/utils.py`
- Modify: `src/triage_agent/agents/infra_agent.py`
- Modify: `src/triage_agent/agents/metrics_agent.py`
- Modify: `src/triage_agent/agents/logs_agent.py`
- Modify: `src/triage_agent/agents/ue_traces_agent.py`
- Test: `tests/unit/test_utils.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_utils.py
from triage_agent.utils import parse_timestamp, extract_log_level, parse_loki_response


def test_parse_timestamp_utc():
    ts = "2024-01-01T12:00:00Z"
    result = parse_timestamp(ts)
    assert isinstance(result, float)
    assert result == 1704110400.0


def test_parse_timestamp_with_offset():
    ts = "2024-01-01T13:00:00+01:00"
    result = parse_timestamp(ts)
    assert result == 1704110400.0


def test_extract_log_level_fatal():
    assert extract_log_level("FATAL: core dump") == "FATAL"


def test_extract_log_level_default():
    assert extract_log_level("no level here") == "INFO"


def test_parse_loki_response_empty():
    assert parse_loki_response({}) == []


def test_parse_loki_response_basic():
    data = {
        "data": {
            "result": [
                {
                    "stream": {"k8s_pod_name": "amf-abc"},
                    "values": [["1700000000123456789", "ERROR something"]],
                }
            ]
        }
    }
    logs = parse_loki_response(data)
    assert len(logs) == 1
    assert logs[0]["pod"] == "amf-abc"
    assert logs[0]["level"] == "ERROR"
    assert logs[0]["timestamp"] == 1700000000
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_utils.py -v
```
Expected: `ModuleNotFoundError: No module named 'triage_agent.utils'`

**Step 3: Write `src/triage_agent/utils.py`**

```python
"""Shared utility functions for TriageAgent pipeline."""

from datetime import UTC, datetime
from typing import Any


def parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp from alert payload. Returns Unix epoch seconds."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC if dt.tzinfo is None else dt.tzinfo).timestamp()


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
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_utils.py -v
```
Expected: All 6 tests PASS.

**Step 5: Replace local definitions in each agent**

In `infra_agent.py`: remove `parse_timestamp` definition (lines ~236-239), add import:
```python
from triage_agent.utils import parse_timestamp
```

In `metrics_agent.py`: remove `parse_timestamp` definition (lines ~21-26), add import:
```python
from triage_agent.utils import parse_timestamp
```

In `logs_agent.py`: remove `parse_timestamp` (lines ~28-33), `_extract_log_level` (lines ~118-124), `_parse_loki_response` (lines ~127-144). Add import:
```python
from triage_agent.utils import extract_log_level, parse_loki_response, parse_timestamp
```
Update `_parse_loki_response` call sites to `parse_loki_response`.
Update `_extract_log_level` call site in `_parse_loki_response` removal (no longer needed — it's in the util).

In `ue_traces_agent.py`: remove `parse_timestamp`, `_extract_log_level`, `_parse_loki_response`. Add import:
```python
from triage_agent.utils import extract_log_level, parse_loki_response, parse_timestamp
```
Update `_parse_loki_response` call site to `parse_loki_response`.

In `mcp/client.py`: remove `_extract_log_level` method, replace its one call site with the imported util:
```python
from triage_agent.utils import extract_log_level
# ...
"level": extract_log_level(value[1]),
```

**Step 6: Run type check and tests**

```bash
mypy src/triage_agent/utils.py src/triage_agent/agents/ src/triage_agent/mcp/ --strict
pytest tests/unit/ -v
```
Expected: No new type errors; all existing unit tests pass.

**Step 7: Commit**

```bash
git add src/triage_agent/utils.py src/triage_agent/agents/infra_agent.py \
        src/triage_agent/agents/metrics_agent.py src/triage_agent/agents/logs_agent.py \
        src/triage_agent/agents/ue_traces_agent.py src/triage_agent/mcp/client.py \
        tests/unit/test_utils.py
git commit -m "refactor: extract shared parse_timestamp/extract_log_level/parse_loki_response utilities"
```

---

### Task 2: Fix `trace_deviations` type mismatch

**Problem:**
- `state.py` declares `trace_deviations: list[dict] | None`
- `ue_traces_agent.py` returns a `dict[str, list[dict]]` (keyed by dag_name)
- `rca_agent.py` passes it to `format_trace_deviations_for_prompt()` expecting `list[dict] | None`
- Early-return in `ue_traces_agent.py` returns `{}` (empty dict) not `None` / `[]`
- Gap-check `== []` in `rca_agent.py` never fires because the value is a dict

**Decision:** The richer `dict[str, list[dict]]` form is more useful to the LLM. Update the state type and all consumers to use it.

**Files:**
- Modify: `src/triage_agent/state.py`
- Modify: `src/triage_agent/agents/ue_traces_agent.py`
- Modify: `src/triage_agent/agents/rca_agent.py`
- Test: `tests/unit/test_rca_agent.py` (add/update)

**Step 1: Write the failing test**

```python
# tests/unit/test_rca_agent.py  (add this test)
from triage_agent.agents.rca_agent import format_trace_deviations_for_prompt, identify_evidence_gaps
from triage_agent.graph import get_initial_state


def test_format_trace_deviations_dict():
    deviations = {
        "registration_general": [{"deviation_point": 3, "expected": "AMF sends NAS", "actual": "timeout"}],
        "authentication_5g_aka": [],
    }
    result = format_trace_deviations_for_prompt(deviations)
    assert "registration_general" in result
    assert "deviation_point" in result


def test_format_trace_deviations_none():
    assert format_trace_deviations_for_prompt(None) == "No UE trace deviations available."


def test_format_trace_deviations_empty_dict():
    assert format_trace_deviations_for_prompt({}) == "No UE trace deviations available."


def test_identify_evidence_gaps_empty_trace_deviations():
    """trace_deviations being {} should count as missing."""
    import uuid
    alert = {"labels": {"alertname": "test"}, "startsAt": "2024-01-01T12:00:00Z"}
    state = get_initial_state(alert, str(uuid.uuid4()))
    state["trace_deviations"] = {}
    gaps = identify_evidence_gaps(state)
    assert "UE trace analysis needed" in gaps
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_rca_agent.py::test_format_trace_deviations_dict -v
```
Expected: FAIL — wrong type handling.

**Step 3: Update `state.py`**

Change line 28:
```python
# Before
trace_deviations: list[dict] | None  # Per-IMSI deviation results from Memgraph comparison

# After
trace_deviations: dict[str, list[dict]] | None  # {dag_name: [deviation_dicts]} from Memgraph comparison
```

**Step 4: Update `ue_traces_agent.py` early-return (line ~313)**

```python
# Before
return {"discovered_imsis": [], "traces_ready": False, "trace_deviations": {}}

# After
return {"discovered_imsis": [], "traces_ready": False, "trace_deviations": None}
```

**Step 5: Update `rca_agent.py` — `format_trace_deviations_for_prompt`**

```python
def format_trace_deviations_for_prompt(deviations: dict[str, list[dict[str, Any]]] | None) -> str:
    if not deviations:
        return "No UE trace deviations available."
    return json.dumps(deviations, indent=2)
```

**Step 6: Update `rca_agent.py` — `identify_evidence_gaps` gap check (line ~327)**

```python
# Before
if not state.get("trace_deviations") or state.get("trace_deviations") == []:

# After
if not state.get("trace_deviations"):
```

**Step 7: Run tests**

```bash
pytest tests/unit/test_rca_agent.py -v
mypy src/triage_agent/state.py src/triage_agent/agents/ue_traces_agent.py \
     src/triage_agent/agents/rca_agent.py --strict
```
Expected: All new tests PASS; no new type errors.

**Step 8: Commit**

```bash
git add src/triage_agent/state.py src/triage_agent/agents/ue_traces_agent.py \
        src/triage_agent/agents/rca_agent.py tests/unit/test_rca_agent.py
git commit -m "fix: correct trace_deviations type from list to dict[str, list[dict]]"
```

---

### Task 3: Fix `procedure_name` (singular) → `procedure_names` (list) in RCAAgent

**Problem:** `rca_agent.py` calls `state.get("procedure_name")` (singular) — a key that does not exist in `TriageState`. The state has `procedure_names: list[str]`. Both `rca_agent_first_attempt` and `generate_final_report` are affected.

**Decision:** Join the list with `", "` when formatting for the LLM prompt.

**Files:**
- Modify: `src/triage_agent/agents/rca_agent.py`
- Test: `tests/unit/test_rca_agent.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_rca_agent.py (add)
from triage_agent.agents.rca_agent import generate_final_report
from triage_agent.graph import get_initial_state
import uuid


def test_generate_final_report_uses_procedure_names():
    alert = {"labels": {"alertname": "test"}, "startsAt": "2024-01-01T12:00:00Z"}
    state = get_initial_state(alert, str(uuid.uuid4()))
    state["procedure_names"] = ["registration_general", "authentication_5g_aka"]
    state["layer"] = "application"
    state["root_nf"] = "AMF"
    state["failure_mode"] = "timeout"
    state["confidence"] = 0.85
    report = generate_final_report(state)
    assert report["procedure_name"] == "registration_general, authentication_5g_aka"
    assert report["procedure_name"] != "unknown"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_rca_agent.py::test_generate_final_report_uses_procedure_names -v
```
Expected: FAIL — `procedure_name` key missing, returns `"unknown"`.

**Step 3: Fix `generate_final_report` in `rca_agent.py`**

```python
def generate_final_report(state: TriageState) -> dict[str, Any]:
    procedure_names = state.get("procedure_names") or []
    procedure_name_str = ", ".join(procedure_names) if procedure_names else "unknown"
    return {
        "incident_id": state["incident_id"],
        "procedure_name": procedure_name_str,   # was state.get("procedure_name", "unknown")
        # ... rest unchanged
    }
```

**Step 4: Fix `rca_agent_first_attempt` prompt formatting**

```python
# In rca_agent_first_attempt, replace:
procedure_name=state.get("procedure_name", "unknown"),

# With:
procedure_name=", ".join(state.get("procedure_names") or ["unknown"]),
```

**Step 5: Run tests**

```bash
pytest tests/unit/test_rca_agent.py -v
mypy src/triage_agent/agents/rca_agent.py --strict
```
Expected: PASS.

**Step 6: Commit**

```bash
git add src/triage_agent/agents/rca_agent.py tests/unit/test_rca_agent.py
git commit -m "fix: use procedure_names (list) instead of missing procedure_name key in rca_agent"
```

---

### Task 4: Fix `dag` (singular) → `dags` (list) in RCAAgent prompt

**Problem:** `rca_agent_first_attempt` accesses `state.get("dag")` which does not exist in `TriageState`. The state has `dags: list[dict]`. The RCA prompt gets `null` for the DAG, making the DAG-guided analysis useless.

**Decision:** Pass the full list of DAGs as JSON. Update the prompt template label from `PROCEDURE DAG:` to `PROCEDURE DAGs:`.

**Files:**
- Modify: `src/triage_agent/agents/rca_agent.py`
- Test: `tests/unit/test_rca_agent.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_rca_agent.py (add)
def test_rca_prompt_includes_dag_content(monkeypatch):
    """RCA prompt must include actual DAG content, not null."""
    import uuid
    from triage_agent.agents.rca_agent import RCA_PROMPT_TEMPLATE
    from triage_agent.graph import get_initial_state

    alert = {"labels": {"alertname": "test"}, "startsAt": "2024-01-01T12:00:00Z"}
    state = get_initial_state(alert, str(uuid.uuid4()))
    state["procedure_names"] = ["registration_general"]
    state["dags"] = [{"name": "registration_general", "phases": [], "all_nfs": ["AMF"]}]
    state["infra_score"] = 0.1
    state["evidence_quality_score"] = 0.5

    from triage_agent.agents import rca_agent as ra
    # Capture the prompt by patching llm_analyze_evidence
    captured = {}
    def fake_llm(prompt: str, timeout=None):
        captured["prompt"] = prompt
        return {
            "layer": "application", "root_nf": "AMF", "failure_mode": "timeout",
            "failed_phase": None, "confidence": 0.9, "evidence_chain": [],
            "alternative_hypotheses": [], "reasoning": "test",
        }
    monkeypatch.setattr(ra, "llm_analyze_evidence", fake_llm)
    ra.rca_agent_first_attempt(state)
    assert "registration_general" in captured["prompt"]
    assert "null" not in captured["prompt"].split("PROCEDURE DAG")[1][:50]
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_rca_agent.py::test_rca_prompt_includes_dag_content -v
```
Expected: FAIL — DAG content is `null` in prompt.

**Step 3: Fix `rca_agent_first_attempt`**

```python
# Before
dag_json=json.dumps(state.get("dag"), indent=2),

# After
dag_json=json.dumps(state.get("dags"), indent=2),
```

Also update the prompt template label (cosmetic, for LLM clarity):
```python
# In RCA_PROMPT_TEMPLATE, change:
PROCEDURE DAG:
{dag_json}

# To:
PROCEDURE DAGs (reference procedures for this alert):
{dag_json}
```

**Step 4: Run tests**

```bash
pytest tests/unit/test_rca_agent.py -v
mypy src/triage_agent/agents/rca_agent.py --strict
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/triage_agent/agents/rca_agent.py tests/unit/test_rca_agent.py
git commit -m "fix: pass dags list (not missing 'dag' key) to RCA prompt"
```

---

### Task 5: Fix `ingest_captured_trace` event schema mismatch

**Problem:**
- `contract_imsi_trace()` produces `{"timestamp": ..., "nf": ..., "message": ...}`
- `ingest_captured_trace()` Cypher writes `event.order` and `event.action` — both always `null`
- `detect_deviation()` compares `event.action CONTAINS refStep.action` — always fails silently

**Decision:** Align the Cypher to use the fields that are actually present (`message`, `nf`, `timestamp`). Add an `order` field to `contract_imsi_trace` output (enumerated index).

**Files:**
- Modify: `src/triage_agent/agents/ue_traces_agent.py`
- Modify: `src/triage_agent/memgraph/connection.py`
- Test: `tests/unit/test_ue_traces_agent.py`
- Test: `tests/unit/test_memgraph_connection.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_ue_traces_agent.py (add)
from triage_agent.agents.ue_traces_agent import contract_imsi_trace


def test_contract_imsi_trace_includes_order():
    raw = [
        {"timestamp": 100, "pod": "amf-abc", "message": "SVC_REQUEST"},
        {"timestamp": 90,  "pod": "smf-xyz", "message": "PDU_SESSION"},
    ]
    trace = contract_imsi_trace(raw, "123456789012345")
    assert trace["imsi"] == "123456789012345"
    # Events sorted by timestamp
    assert trace["events"][0]["timestamp"] == 90
    assert trace["events"][1]["timestamp"] == 100
    # Each event must have 'order', 'message', 'nf', 'timestamp'
    for i, event in enumerate(trace["events"]):
        assert event["order"] == i
        assert "message" in event
        assert "nf" in event
        assert "timestamp" in event
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_ue_traces_agent.py::test_contract_imsi_trace_includes_order -v
```
Expected: FAIL — `order` key missing.

**Step 3: Fix `contract_imsi_trace` in `ue_traces_agent.py`**

```python
def contract_imsi_trace(
    raw_trace: list[dict[str, Any]], imsi: str
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for entry in raw_trace:
        events.append({
            "timestamp": entry.get("timestamp", 0),
            "nf": extract_nf_from_pod_name(entry.get("pod", "unknown")),
            "message": entry.get("message", ""),
        })
    events.sort(key=lambda e: e["timestamp"])
    # Add sequential order after sorting
    for i, event in enumerate(events):
        event["order"] = i
    return {"imsi": imsi, "events": events}
```

**Step 4: Fix `ingest_captured_trace` Cypher in `connection.py`**

```python
def ingest_captured_trace(
    self,
    incident_id: str,
    imsi: str,
    events: list[dict[str, Any]],
) -> None:
    """Ingest a captured IMSI trace into Memgraph."""
    query = """
    CREATE (t:CapturedTrace {incident_id: $incident_id, imsi: $imsi})
    WITH t
    UNWIND $events AS event
    CREATE (t)-[:EVENT]->(e:TraceEvent {
        order: event.order,
        message: event.message,
        timestamp: event.timestamp,
        nf: event.nf
    })
    """
    self.execute_cypher_write(
        query,
        {"incident_id": incident_id, "imsi": imsi, "events": events},
    )
```

**Step 5: Fix `detect_deviation` Cypher to match on `message` instead of `action`**

```python
def detect_deviation(self, incident_id: str, imsi: str, dag_name: str) -> dict[str, Any] | None:
    query = """
    MATCH (ref:ReferenceTrace {name: $dag_name})-[:STEP]->(refStep:RefEvent)
    MATCH (trace:CapturedTrace {incident_id: $incident_id, imsi: $imsi})-[:EVENT]->(event:TraceEvent)
    WHERE refStep.order = event.order AND NOT event.message CONTAINS refStep.action
    RETURN refStep.order AS deviation_point,
           refStep.action AS expected,
           event.message AS actual,
           refStep.nf AS expected_nf,
           event.nf AS actual_nf
    ORDER BY refStep.order
    LIMIT 1
    """
    results = self.execute_cypher(
        query,
        {"dag_name": dag_name, "incident_id": incident_id, "imsi": imsi},
    )
    return results[0] if results else None
```

**Step 6: Run tests**

```bash
pytest tests/unit/test_ue_traces_agent.py tests/unit/test_memgraph_connection.py -v
mypy src/triage_agent/agents/ue_traces_agent.py src/triage_agent/memgraph/connection.py --strict
```
Expected: PASS.

**Step 7: Commit**

```bash
git add src/triage_agent/agents/ue_traces_agent.py src/triage_agent/memgraph/connection.py \
        tests/unit/test_ue_traces_agent.py tests/unit/test_memgraph_connection.py
git commit -m "fix: align contract_imsi_trace event schema with ingest_captured_trace Cypher"
```

---

### Task 6: Fix LangGraph double-invocation of `rca_agent`

**Problem:** `graph.py` adds two direct edges into `rca_agent`:
```
infra_agent → rca_agent
evidence_quality → rca_agent
```
In LangGraph, each edge triggers the target node independently. `rca_agent` runs **twice** — once when `infra_agent` finishes (before evidence is ready) and once when `evidence_quality` finishes. The fix is a barrier/join node that waits for both branches before allowing RCA to proceed.

**Files:**
- Modify: `src/triage_agent/graph.py`
- Test: `tests/unit/test_graph.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_graph.py (add or create)
def test_rca_agent_has_single_entry_point():
    """rca_agent must not be reachable via two independent parallel edges."""
    from triage_agent.graph import create_workflow
    wf = create_workflow()
    graph = wf.get_graph()
    # Count distinct edges leading directly into rca_agent
    rca_incoming = [e for e in graph.edges if e[1] == "rca_agent"]
    assert len(rca_incoming) == 1, (
        f"rca_agent has {len(rca_incoming)} incoming edges — expected 1 (from join node). "
        f"Edges: {rca_incoming}"
    )
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_graph.py::test_rca_agent_has_single_entry_point -v
```
Expected: FAIL — `rca_agent` has 2 incoming edges.

**Step 3: Add a `join_for_rca` node to `graph.py`**

In `graph.py`, add a no-op join function and wire it correctly:

```python
def join_for_rca(state: TriageState) -> TriageState:
    """Barrier: waits for both infra_agent and evidence_quality before RCA."""
    return state
```

Then in `create_workflow()`:

```python
workflow.add_node("join_for_rca", join_for_rca)

# Replace the two direct edges into rca_agent:
# OLD:
#   workflow.add_edge("infra_agent", "rca_agent")
#   workflow.add_edge("evidence_quality", "rca_agent")
# NEW:
workflow.add_edge("infra_agent", "join_for_rca")
workflow.add_edge("evidence_quality", "join_for_rca")
workflow.add_edge("join_for_rca", "rca_agent")
```

**Step 4: Run tests**

```bash
pytest tests/unit/test_graph.py -v
mypy src/triage_agent/graph.py --strict
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/triage_agent/graph.py tests/unit/test_graph.py
git commit -m "fix: add join_for_rca barrier node to prevent double rca_agent invocation"
```

---

### Task 7: Fix `second_attempt_complete` never being set

**Problem:** `second_attempt_complete` is declared in state, initialized to `False`, but never set to `True`. The retry loop also doesn't re-collect evidence — `increment_attempt → rca_agent` re-runs LLM with identical evidence.

**Decision (minimal fix):** Set `second_attempt_complete = True` after a retry attempt completes (i.e., when `attempt_count > 1` at the start of `rca_agent_first_attempt`). This makes the flag semantically correct and removes a confusing dead field. The evidence re-collection gap (Bug 7) is a separate design decision left for a future plan.

**Files:**
- Modify: `src/triage_agent/agents/rca_agent.py`
- Test: `tests/unit/test_rca_agent.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_rca_agent.py (add)
def test_second_attempt_complete_set_on_retry(monkeypatch):
    import uuid
    from triage_agent.agents import rca_agent as ra
    from triage_agent.graph import get_initial_state

    alert = {"labels": {"alertname": "test"}, "startsAt": "2024-01-01T12:00:00Z"}
    state = get_initial_state(alert, str(uuid.uuid4()))
    state["attempt_count"] = 2   # simulating a retry
    state["dags"] = []
    state["procedure_names"] = []

    def fake_llm(prompt, timeout=None):
        return {
            "layer": "application", "root_nf": "AMF", "failure_mode": "timeout",
            "failed_phase": None, "confidence": 0.9, "evidence_chain": [],
            "alternative_hypotheses": [], "reasoning": "test",
        }
    monkeypatch.setattr(ra, "llm_analyze_evidence", fake_llm)

    result = ra.rca_agent_first_attempt(state)
    assert result["second_attempt_complete"] is True


def test_second_attempt_complete_not_set_on_first_attempt(monkeypatch):
    import uuid
    from triage_agent.agents import rca_agent as ra
    from triage_agent.graph import get_initial_state

    alert = {"labels": {"alertname": "test"}, "startsAt": "2024-01-01T12:00:00Z"}
    state = get_initial_state(alert, str(uuid.uuid4()))
    state["attempt_count"] = 1
    state["dags"] = []
    state["procedure_names"] = []

    def fake_llm(prompt, timeout=None):
        return {
            "layer": "application", "root_nf": "AMF", "failure_mode": "timeout",
            "failed_phase": None, "confidence": 0.9, "evidence_chain": [],
            "alternative_hypotheses": [], "reasoning": "test",
        }
    monkeypatch.setattr(ra, "llm_analyze_evidence", fake_llm)

    result = ra.rca_agent_first_attempt(state)
    assert result["second_attempt_complete"] is False
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_rca_agent.py::test_second_attempt_complete_set_on_retry -v
```
Expected: FAIL.

**Step 3: Update `rca_agent_first_attempt`**

At the end of `rca_agent_first_attempt`, before `return state`, add:

```python
# Mark second attempt complete if this was a retry
if state.get("attempt_count", 1) > 1:
    state["second_attempt_complete"] = True
```

**Step 4: Run tests**

```bash
pytest tests/unit/test_rca_agent.py -v
mypy src/triage_agent/agents/rca_agent.py --strict
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/triage_agent/agents/rca_agent.py tests/unit/test_rca_agent.py
git commit -m "fix: set second_attempt_complete=True on retry attempts in rca_agent"
```

---

### Task 8: Remove dead code `run_deviation_detection`

**Problem:** `ue_traces_agent.py` defines two nearly identical functions; `run_deviation_detection` is never called. Only `run_deviation_detection_for_dag` is used.

**Files:**
- Modify: `src/triage_agent/agents/ue_traces_agent.py`
- Test: `tests/unit/test_ue_traces_agent.py`

**Step 1: Write the verification test**

```python
# tests/unit/test_ue_traces_agent.py (add)
def test_run_deviation_detection_removed():
    """Dead code run_deviation_detection should not exist."""
    import triage_agent.agents.ue_traces_agent as uta
    assert not hasattr(uta, "run_deviation_detection"), (
        "run_deviation_detection is dead code and should be removed"
    )
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_ue_traces_agent.py::test_run_deviation_detection_removed -v
```
Expected: FAIL.

**Step 3: Remove `run_deviation_detection` from `ue_traces_agent.py`**

Delete lines ~250-276 (the `run_deviation_detection` function). Keep `run_deviation_detection_for_dag`.

**Step 4: Run tests**

```bash
pytest tests/unit/test_ue_traces_agent.py -v
mypy src/triage_agent/agents/ue_traces_agent.py --strict
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/triage_agent/agents/ue_traces_agent.py tests/unit/test_ue_traces_agent.py
git commit -m "fix: remove dead code run_deviation_detection (use run_deviation_detection_for_dag)"
```

---

### Task 9: Fix misleading comment in `metrics_agent.py`

**Problem:** Comment says "avoids LangGraph parallel-merge conflict with infra_agent (both start from START)" — but `metrics_agent` starts from `dag_mapper`, not `START`. Only `infra_agent` and `dag_mapper` start from `START`.

**Files:**
- Modify: `src/triage_agent/agents/metrics_agent.py`

**Step 1: Fix the comment**

At `metrics_agent.py:161`, change:
```python
# Before
# Return only the key this agent writes — avoids LangGraph parallel-merge conflict
# with infra_agent (both start from START in the same step).

# After
# Return only the key this agent writes — avoids LangGraph parallel-merge conflict
# with logs_agent and traces_agent (all three fan out from dag_mapper in parallel).
```

**Step 2: Commit**

```bash
git add src/triage_agent/agents/metrics_agent.py
git commit -m "fix: correct misleading comment about parallel execution in metrics_agent"
```

---

### Task 10: Add retry logic to `execute_cypher_write`

**Problem:** `execute_cypher_write` has no retry on `ServiceUnavailable`/`TransientError`, unlike `execute_cypher`. Write operations to Memgraph (trace ingestion) are equally susceptible to transient errors.

**Files:**
- Modify: `src/triage_agent/memgraph/connection.py`
- Test: `tests/unit/test_memgraph_connection.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_memgraph_connection.py (add)
from unittest.mock import MagicMock, patch, call
from neo4j.exceptions import ServiceUnavailable
from triage_agent.memgraph.connection import MemgraphConnection


def test_execute_cypher_write_retries_on_service_unavailable():
    conn = MemgraphConnection.__new__(MemgraphConnection)
    conn._max_retries = 3

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.run.side_effect = [
        ServiceUnavailable("down"),
        ServiceUnavailable("down"),
        None,   # succeeds on 3rd attempt
    ]

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    conn._driver = mock_driver

    conn.execute_cypher_write("MERGE (n:Test {id: 1})")
    assert mock_session.run.call_count == 3
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_memgraph_connection.py::test_execute_cypher_write_retries_on_service_unavailable -v
```
Expected: FAIL — no retries happen, exception propagates.

**Step 3: Update `execute_cypher_write` in `connection.py`**

```python
def execute_cypher_write(
    self,
    query: str,
    params: dict[str, Any] | None = None,
    max_retries: int | None = None,
) -> None:
    """Execute a write Cypher query with retry logic."""
    retries = max_retries if max_retries is not None else self._max_retries
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with self._driver.session() as session:
                session.run(query, params or {})
                return
        except (ServiceUnavailable, TransientError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2**attempt)

    if last_error:
        raise last_error
    raise RuntimeError("Unexpected error in execute_cypher_write")  # pragma: no cover
```

**Step 4: Run tests**

```bash
pytest tests/unit/test_memgraph_connection.py -v
mypy src/triage_agent/memgraph/connection.py --strict
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/triage_agent/memgraph/connection.py tests/unit/test_memgraph_connection.py
git commit -m "fix: add retry logic to execute_cypher_write (mirrors execute_cypher)"
```

---

### Task 11: Fix health check to include Loki in overall status

**Problem:** `webhook.py` health check sets `overall_status` based only on `memgraph_ok and prometheus_ok`, ignoring `loki_ok`. A fully-down Loki is invisible.

**Files:**
- Modify: `src/triage_agent/api/webhook.py`
- Test: `tests/unit/test_webhook.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_webhook.py (add)
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock


def test_health_check_degraded_when_loki_down():
    from triage_agent.api.webhook import app
    client = TestClient(app)

    with patch("triage_agent.api.webhook.get_memgraph") as mock_mg, \
         patch("triage_agent.api.webhook.MCPClient") as mock_mcp_cls:
        mock_mg.return_value.health_check.return_value = True

        mock_mcp = AsyncMock()
        mock_mcp.health_check_prometheus = AsyncMock(return_value=True)
        mock_mcp.health_check_loki = AsyncMock(return_value=False)
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=None)
        mock_mcp_cls.return_value = mock_mcp

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["loki"] is False
        assert data["status"] == "degraded"
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_webhook.py::test_health_check_degraded_when_loki_down -v
```
Expected: FAIL — status is `"healthy"` despite loki being down.

**Step 3: Fix `webhook.py` health check**

```python
# Before
overall_status = "healthy" if (memgraph_ok and prometheus_ok) else "degraded"

# After
overall_status = "healthy" if (memgraph_ok and prometheus_ok and loki_ok) else "degraded"
```

**Step 4: Run tests**

```bash
pytest tests/unit/test_webhook.py -v
mypy src/triage_agent/api/webhook.py --strict
```
Expected: PASS.

**Step 5: Commit**

```bash
git add src/triage_agent/api/webhook.py tests/unit/test_webhook.py
git commit -m "fix: include loki_ok in overall health status determination"
```

---

## Final Verification

```bash
pytest tests/unit/ -v --tb=short
mypy src/ --strict
ruff check src/
```

All tests must pass, no new mypy errors, no ruff violations.

---

## Bug Backlog (Out of Scope — Design Decisions)

These issues require architectural discussion and are not addressed in this plan:

- **`evidence_gaps` never consumed:** The retry loop re-runs RCA with the same data rather than re-collecting evidence per identified gaps. Fixing this requires routing `increment_attempt` back to the data-collection layer rather than directly to `rca_agent`.
- **`infra_agent` MCP TODO:** The MCP client call is stubbed out (`metrics: dict[str, Any] = {}`). Wiring this up is a separate feature task.
