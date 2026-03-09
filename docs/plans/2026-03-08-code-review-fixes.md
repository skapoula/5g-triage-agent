# Code Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 13 issues raised in the code review: topology, correctness, robustness, type-safety, and cleanup.

**Architecture:** Fixes are strictly surgical — no new features. The most structurally significant change is restoring `join_for_rca` as a real barrier node (owned by `rca_agent.py`) that also pre-compresses evidence for the LLM, guaranteeing `infra_agent` data always reaches the RCA step. All other tasks are independent.

**Tech Stack:** Python 3.13, LangGraph, FastAPI, Pydantic v2, langchain-openai, pytest, mypy --strict, ruff

---

## Pre-flight

```bash
cd /home/agent/workspace/5g-triage-agent
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/unit/ -v --tb=short        # baseline: 532/533 pass (1 known fail due to uncommitted config change)
mypy src/ --strict 2>&1 | grep "error:" | wc -l   # baseline: 14 errors
ruff check src/ 2>&1 | grep "^[A-Z]" | wc -l      # baseline: 8 errors
```

---

## Task 1: Move `compress_dag` and `compress_trace_deviations` to `utils.py`

**Why first:** Task 2 moves evidence-joining into `rca_agent.py`; having these functions already in `utils.py` keeps `rca_agent.py` clean and consistent with where `count_tokens` already lives.

**Files:**
- Modify: `src/triage_agent/utils.py`
- Modify: `src/triage_agent/agents/rca_agent.py`
- Modify: `tests/unit/test_utils.py`
- Modify: `tests/unit/test_rca_agent.py`

### Step 1: Write the failing tests for `compress_dag` and `compress_trace_deviations` in `test_utils.py`

Add to the bottom of `tests/unit/test_utils.py`:

```python
from triage_agent.utils import compress_dag, compress_trace_deviations


class TestCompressDag:
    def test_returns_empty_for_none(self) -> None:
        assert compress_dag(None, 1000) == []

    def test_returns_as_is_when_within_budget(self) -> None:
        dags = [{"phases": [{"order": 1, "keywords": ["k"], "success_log": "ok"}]}]
        result = compress_dag(dags, 10_000)
        assert result == dags

    def test_strips_keywords_and_success_log_when_over_budget(self) -> None:
        phase = {"order": 1, "keywords": ["k"] * 500, "success_log": "ok", "failure_patterns": ["*fail*"]}
        dags = [{"phases": [phase]}]
        result = compress_dag(dags, 50)
        assert "keywords" not in result[0]["phases"][0]
        assert "success_log" not in result[0]["phases"][0]

    def test_all_zero_phases_returns_stable_result(self) -> None:
        """compress_dag must not raise or loop infinitely when all phases are empty after step 3."""
        dags = [{"phases": []} for _ in range(20)]
        result = compress_dag(dags, 1)
        assert isinstance(result, list)


class TestCompressTraceDeviations:
    def test_returns_empty_for_none(self) -> None:
        assert compress_trace_deviations(None, 1000) == {}

    def test_slices_per_dag_to_max(self) -> None:
        devs = {"dag_a": [{"d": i} for i in range(10)]}
        result = compress_trace_deviations(devs, 10_000)
        assert len(result["dag_a"]) <= 3   # default rca_max_deviations_per_dag
```

### Step 2: Run tests to verify they fail

```bash
pytest tests/unit/test_utils.py::TestCompressDag tests/unit/test_utils.py::TestCompressTraceDeviations -v
```

Expected: `ImportError: cannot import name 'compress_dag' from 'triage_agent.utils'`

### Step 3: Move the two functions to `utils.py`

In `src/triage_agent/utils.py`, add at the top:
```python
import json
import logging
```
(already present — skip if already there)

Append after `save_artifact`:

```python
def compress_dag(
    dags: list[dict[str, Any]] | None,
    token_budget: int,
) -> list[dict[str, Any]]:
    """Compress DAG structures to fit within token_budget.

    Stripping cascade (each step checked against budget):
        1. Return as-is if within budget.
        2. Strip 'keywords' and 'success_log' from all phases.
        3. Keep only phases that have non-empty 'failure_patterns'.
        4. Truncate phases per DAG (always keep first + last phases).
    """
    if not dags:
        return []

    def _fits(d: list[dict[str, Any]]) -> bool:
        return count_tokens(json.dumps(d)) <= token_budget

    if _fits(dags):
        return dags

    # Step 2: strip keywords and success_log
    stripped: list[dict[str, Any]] = []
    for dag in dags:
        phases = [
            {k: v for k, v in phase.items() if k not in ("keywords", "success_log")}
            for phase in dag.get("phases", [])
        ]
        stripped.append({**dag, "phases": phases})

    if _fits(stripped):
        return stripped

    # Step 3: keep only phases with failure_patterns
    fp_only: list[dict[str, Any]] = []
    for dag in stripped:
        phases = [p for p in dag.get("phases", []) if p.get("failure_patterns")]
        fp_only.append({**dag, "phases": phases})

    if _fits(fp_only):
        return fp_only

    # Guard: if all DAGs have zero phases after filtering, no truncation possible
    if not any(d.get("phases") for d in fp_only):
        return fp_only

    # Step 4: truncate phases, always keeping first and last
    result = fp_only
    for max_phases in range(len(max((d.get("phases", []) for d in result), key=len, default=[])), 0, -1):
        truncated = []
        for dag in result:
            phases = dag.get("phases", [])
            if len(phases) > max_phases:
                keep = [phases[0]] if phases else []
                middle = phases[1:-1][:max(0, max_phases - 2)]
                last = [phases[-1]] if len(phases) > 1 else []
                phases = keep + middle + last
            truncated.append({**dag, "phases": phases})
        if _fits(truncated):
            logger.warning("DAG compressed: truncated to %d phases per DAG", max_phases)
            return truncated

    return result


def compress_trace_deviations(
    deviations: dict[str, list[dict[str, Any]]] | None,
    token_budget: int,
) -> dict[str, list[dict[str, Any]]]:
    """Compress trace deviations to fit within token_budget.

    Slices each DAG's deviation list to cfg.rca_max_deviations_per_dag,
    then drops DAGs with empty lists if still over budget.
    """
    from triage_agent.config import get_config  # deferred to avoid circular import

    if not deviations:
        return {}

    cfg = get_config()
    max_per_dag = cfg.rca_max_deviations_per_dag

    sliced = {dag: devs[:max_per_dag] for dag, devs in deviations.items()}

    if count_tokens(json.dumps(sliced)) <= token_budget:
        return sliced

    # Drop empty DAGs first
    non_empty = {dag: devs for dag, devs in sliced.items() if devs}
    if count_tokens(json.dumps(non_empty)) <= token_budget:
        return non_empty

    # Drop DAGs with fewest deviations until within budget
    dag_names = sorted(non_empty.keys(), key=lambda d: len(non_empty[d]))
    while dag_names and count_tokens(json.dumps({d: non_empty[d] for d in dag_names})) > token_budget:
        dag_names.pop(0)

    return {d: non_empty[d] for d in dag_names}
```

### Step 4: Replace bodies in `rca_agent.py` with imports

In `src/triage_agent/agents/rca_agent.py`:

1. At the top, update the import from utils:
```python
from triage_agent.utils import compress_dag, compress_trace_deviations, count_tokens
```

2. Delete the full bodies of `compress_dag` (lines 169–227) and `compress_trace_deviations` (lines 230–260). Replace with a one-line re-export comment so callers that import from `rca_agent` still work during transition:
```python
# compress_dag and compress_trace_deviations are now in triage_agent.utils
```

3. In `compress_evidence`, no changes needed — it already calls `compress_dag` and `compress_trace_deviations` as local names, which now resolve to the imported functions.

### Step 5: Update `test_rca_agent.py` imports

Find any test that imports `compress_dag` or `compress_trace_deviations` from `rca_agent` and update to import from `utils`:
```python
from triage_agent.utils import compress_dag, compress_trace_deviations
```

### Step 6: Run tests to verify they pass

```bash
pytest tests/unit/test_utils.py tests/unit/test_rca_agent.py -v
```

Expected: all pass.

### Step 7: Commit

```bash
git add src/triage_agent/utils.py src/triage_agent/agents/rca_agent.py tests/unit/test_utils.py tests/unit/test_rca_agent.py
git commit -m "refactor: move compress_dag + compress_trace_deviations to utils.py"
```

---

## Task 2: Re-add `join_for_rca` as real evidence-joining barrier in `rca_agent.py`

This fixes Issues 1 (dead code / wrong home) and 2 (infra data not guaranteed to reach rca_agent).

**What changes:**
- `TriageState` gets a new field `compressed_evidence: dict[str, str] | None`
- `join_for_rca` moves from `graph.py` (dead noop) → `rca_agent.py` (real compression barrier)
- Graph wired: `infra_agent → join_for_rca`, `evidence_quality → join_for_rca`, `join_for_rca → rca_agent`
- `rca_agent_first_attempt` reads `state["compressed_evidence"]` instead of calling `compress_evidence(state)`

**Files:**
- Modify: `src/triage_agent/state.py`
- Modify: `src/triage_agent/agents/rca_agent.py`
- Modify: `src/triage_agent/graph.py`
- Modify: `tests/unit/test_graph.py`
- Modify: `tests/unit/test_rca_agent.py`

### Step 1: Write failing tests

**In `tests/unit/test_graph.py`**, replace `test_join_for_rca_node_not_in_graph` and `test_rca_agent_has_single_incoming_edge`:

```python
def test_join_for_rca_is_barrier_node(self) -> None:
    """join_for_rca is in the graph and has edges from both infra_agent and evidence_quality."""
    graph = create_workflow().get_graph()
    node_names = list(graph.nodes)
    assert "join_for_rca" in node_names

    edge_pairs = [(e.source, e.target) for e in graph.edges]
    assert ("infra_agent", "join_for_rca") in edge_pairs
    assert ("evidence_quality", "join_for_rca") in edge_pairs
    assert ("join_for_rca", "rca_agent") in edge_pairs

def test_rca_agent_entry_is_join_for_rca(self) -> None:
    """rca_agent's only pipeline entry point is join_for_rca (not evidence_quality directly)."""
    graph = create_workflow().get_graph()
    rca_incoming = {e.source for e in graph.edges if e.target == "rca_agent"}
    pipeline_sources = rca_incoming - {"increment_attempt"}
    assert pipeline_sources == {"join_for_rca"}, (
        f"rca_agent pipeline sources: {pipeline_sources}, expected exactly {{'join_for_rca'}}"
    )
```

**In `tests/unit/test_rca_agent.py`**, add a new test class:

```python
from triage_agent.agents.rca_agent import join_for_rca


class TestJoinForRca:
    def test_returns_compressed_evidence_dict(self, sample_initial_state: TriageState) -> None:
        """join_for_rca returns a delta dict with 'compressed_evidence' key."""
        state = sample_initial_state
        result = join_for_rca(state)
        assert "compressed_evidence" in result
        assert isinstance(result["compressed_evidence"], dict)

    def test_compressed_evidence_has_prompt_keys(self, sample_initial_state: TriageState) -> None:
        """compressed_evidence dict contains all RCA_PROMPT_TEMPLATE placeholder keys."""
        result = join_for_rca(sample_initial_state)
        expected_keys = {
            "infra_findings_json", "dag_json",
            "metrics_formatted", "logs_formatted", "trace_deviations_formatted",
        }
        assert expected_keys.issubset(result["compressed_evidence"].keys())

    def test_join_for_rca_with_infra_findings(self, sample_initial_state: TriageState) -> None:
        """join_for_rca includes infra_findings in compressed_evidence."""
        state = sample_initial_state
        state["infra_findings"] = {"pod_restarts": 3}
        result = join_for_rca(state)
        assert "pod_restarts" in result["compressed_evidence"]["infra_findings_json"]
```

### Step 2: Run tests to verify they fail

```bash
pytest tests/unit/test_graph.py::TestCreateWorkflow::test_join_for_rca_is_barrier_node tests/unit/test_rca_agent.py::TestJoinForRca -v
```

Expected: `FAILED` (join_for_rca not in graph / not importable from rca_agent)

### Step 3: Add `compressed_evidence` to `TriageState` in `state.py`

Add after `evidence_gaps: list[str] | None`:

```python
compressed_evidence: dict[str, str] | None  # pre-compressed evidence sections for the LLM prompt
```

### Step 4: Add `join_for_rca` to `rca_agent.py`

After the `compress_evidence` function (around line 284), add:

```python
@traceable(name="join_for_rca")
def join_for_rca(state: TriageState) -> dict[str, Any]:
    """Barrier node: waits for infra_agent + evidence_quality, then compresses all evidence.

    This is the explicit synchronisation point that guarantees infra_agent data
    is present in state before the LLM prompt is built.  It replaces the previous
    implicit superstep-merge assumption.
    """
    compressed = compress_evidence(state)
    return {"compressed_evidence": compressed}
```

### Step 5: Update `rca_agent_first_attempt` to use pre-compressed evidence

Replace the two lines:
```python
_cfg = get_config()
evidence = compress_evidence(state)
```

With:
```python
_cfg = get_config()
# compressed_evidence is always present — populated by join_for_rca barrier node
evidence = state["compressed_evidence"]
```

### Step 6: Update `graph.py`

**Imports section** — add `join_for_rca` to the import inside `create_workflow`:
```python
from triage_agent.agents.rca_agent import join_for_rca, rca_agent_first_attempt
```

**Remove the dead `join_for_rca` function** from `graph.py` (lines 39–41):
```python
def join_for_rca(state: TriageState) -> TriageState:
    """Barrier node: waits for both infra_agent and evidence_quality before RCA."""
    return state
```

**Add the node and rewire edges** in `create_workflow`:

```python
# Add node
workflow.add_node("join_for_rca", join_for_rca)

# Replace:  workflow.add_edge("evidence_quality", "rca_agent")
# With:
workflow.add_edge("infra_agent", "join_for_rca")
workflow.add_edge("evidence_quality", "join_for_rca")
workflow.add_edge("join_for_rca", "rca_agent")
```

Remove the old line `workflow.add_edge("evidence_quality", "rca_agent")`.

Also **remove** the stale comment about superstep-merge semantics (was lines 107–110).

### Step 7: Update `get_initial_state` in `graph.py`

Add `compressed_evidence=None` to the `TriageState(...)` constructor call.

### Step 8: Update the old `test_rca_agent_has_single_incoming_edge` test

Find and replace the test body (it now expects `join_for_rca`, not `evidence_quality`):
```python
def test_rca_agent_entry_is_join_for_rca(self) -> None:
    """rca_agent's only pipeline entry point is join_for_rca."""
    graph = create_workflow().get_graph()
    rca_incoming = {e.source for e in graph.edges if e.target == "rca_agent"}
    pipeline_sources = rca_incoming - {"increment_attempt"}
    assert pipeline_sources == {"join_for_rca"}
```

Remove the now-redundant `test_join_for_rca_node_not_in_graph` test entirely.

### Step 9: Run all tests

```bash
pytest tests/unit/ -v --tb=short
```

Expected: all pass (previously failing `test_join_for_rca_node_not_in_graph` is now gone; new tests pass).

### Step 10: Commit

```bash
git add src/triage_agent/state.py src/triage_agent/agents/rca_agent.py src/triage_agent/graph.py tests/unit/test_graph.py tests/unit/test_rca_agent.py
git commit -m "feat: re-add join_for_rca as evidence-joining barrier node in rca_agent.py"
```

---

## Task 3: LLM timeout recovery in `rca_agent_first_attempt`

**Why:** After degraded mode was removed, a `TimeoutError` from `llm_analyze_evidence` propagates uncaught, silently crashing the pipeline with no RCA output. LLM timeouts are a realistic production scenario.

**Fix:** Catch `TimeoutError` and return a low-confidence sentinel result so `finalize_report` still produces a (partial) report.

**Files:**
- Modify: `src/triage_agent/agents/rca_agent.py`
- Modify: `tests/unit/test_rca_agent.py`

### Step 1: Write failing test

In `tests/unit/test_rca_agent.py`, add to a new class:

```python
class TestRcaAgentTimeoutRecovery:
    def test_timeout_returns_sentinel_not_raises(self, sample_initial_state: TriageState) -> None:
        """rca_agent_first_attempt must NOT raise on TimeoutError — returns low-confidence sentinel."""
        sample_initial_state["compressed_evidence"] = {
            "infra_findings_json": "{}",
            "dag_json": "[]",
            "metrics_formatted": "No metrics available.",
            "logs_formatted": "No logs available.",
            "trace_deviations_formatted": "No UE trace deviations available.",
        }
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            side_effect=TimeoutError("LLM timed out"),
        ):
            result = rca_agent_first_attempt(sample_initial_state)

        assert result["confidence"] == 0.0
        assert result["root_nf"] == "unknown"
        assert result["failure_mode"] == "llm_timeout"
        assert result["needs_more_evidence"] is False
        assert result["evidence_gaps"] == ["LLM analysis unavailable due to timeout"]

    def test_timeout_sentinel_does_not_trigger_retry(self, sample_initial_state: TriageState) -> None:
        """Timeout sentinel has needs_more_evidence=False so the pipeline finalises rather than retries."""
        sample_initial_state["compressed_evidence"] = {
            "infra_findings_json": "{}",
            "dag_json": "[]",
            "metrics_formatted": "No metrics available.",
            "logs_formatted": "No logs available.",
            "trace_deviations_formatted": "No UE trace deviations available.",
        }
        with patch(
            "triage_agent.agents.rca_agent.llm_analyze_evidence",
            side_effect=TimeoutError("LLM timed out"),
        ):
            result = rca_agent_first_attempt(sample_initial_state)

        assert result["needs_more_evidence"] is False
```

### Step 2: Run to verify failure

```bash
pytest tests/unit/test_rca_agent.py::TestRcaAgentTimeoutRecovery -v
```

Expected: `FAILED` with `TimeoutError`

### Step 3: Wrap LLM call with timeout handler in `rca_agent.py`

In `rca_agent_first_attempt`, replace:
```python
analysis = llm_analyze_evidence(prompt)
```

With:
```python
try:
    analysis = llm_analyze_evidence(prompt)
except TimeoutError:
    logger.warning(
        "LLM timed out for incident %s; returning low-confidence sentinel",
        state.get("incident_id"),
    )
    return {
        "root_nf": "unknown",
        "failure_mode": "llm_timeout",
        "confidence": 0.0,
        "evidence_chain": [],
        "layer": "unknown",
        "needs_more_evidence": False,
        "evidence_gaps": ["LLM analysis unavailable due to timeout"],
    }
```

### Step 4: Run tests

```bash
pytest tests/unit/test_rca_agent.py::TestRcaAgentTimeoutRecovery -v
```

Expected: PASS

### Step 5: Commit

```bash
git add src/triage_agent/agents/rca_agent.py tests/unit/test_rca_agent.py
git commit -m "fix: catch TimeoutError in rca_agent_first_attempt, return low-confidence sentinel"
```

---

## Task 4: Implement `rca_log_max_message_chars` truncation in `compress_nf_logs`

**Why:** The config field `rca_log_max_message_chars: int = 200` exists, is documented as "max chars per log message before truncation", but `compress_nf_logs` never consumes it. The docstring contradicts the config.

**Fix:** Apply per-entry message truncation. Update the docstring.

**Files:**
- Modify: `src/triage_agent/agents/logs_agent.py`
- Modify: `tests/unit/test_logs_agent.py`

### Step 1: Write failing test

In `tests/unit/test_logs_agent.py`, add a new test:

```python
class TestCompressNfLogsTruncation:
    def test_long_messages_are_truncated(self) -> None:
        """Messages longer than rca_log_max_message_chars are truncated to that length."""
        from unittest.mock import patch
        from triage_agent.agents.logs_agent import compress_nf_logs

        long_msg = "E " + "x" * 500
        logs = {"AMF": [{"level": "ERROR", "message": long_msg, "matched_phase": None, "matched_pattern": None}]}
        with patch("triage_agent.agents.logs_agent.get_config") as mock_cfg:
            mock_cfg.return_value.rca_log_max_message_chars = 50
            mock_cfg.return_value.rca_token_budget_logs = 10_000
            result = compress_nf_logs(logs, ["AMF"], 10_000)

        assert len(result["AMF"][0]["message"]) <= 51  # 50 chars + ellipsis char
        assert result["AMF"][0]["message"].endswith("…")

    def test_short_messages_are_not_truncated(self) -> None:
        from unittest.mock import patch
        from triage_agent.agents.logs_agent import compress_nf_logs

        logs = {"AMF": [{"level": "ERROR", "message": "short", "matched_phase": None, "matched_pattern": None}]}
        with patch("triage_agent.agents.logs_agent.get_config") as mock_cfg:
            mock_cfg.return_value.rca_log_max_message_chars = 200
            mock_cfg.return_value.rca_token_budget_logs = 10_000
            result = compress_nf_logs(logs, ["AMF"], 10_000)

        assert result["AMF"][0]["message"] == "short"
```

### Step 2: Run to verify failure

```bash
pytest tests/unit/test_logs_agent.py::TestCompressNfLogsTruncation -v
```

Expected: FAIL (messages not truncated)

### Step 3: Implement truncation in `compress_nf_logs`

In `src/triage_agent/agents/logs_agent.py`, in the `compress_nf_logs` function:

1. At the start of the function, after `if not logs: return {}`, retrieve the max chars from config:
```python
cfg = get_config()
max_chars = cfg.rca_log_max_message_chars
```

2. Add a helper inline (before the `dag_nf_logs` / `non_dag_nf_logs` split):
```python
def _truncate(entry: dict[str, Any]) -> dict[str, Any]:
    msg = entry.get("message", "")
    if len(msg) > max_chars:
        return {**entry, "message": msg[:max_chars] + "…"}
    return entry
```

3. Apply `_truncate` when building `dag_nf_logs` and the `qualifying` list:
```python
# In the for nf, entries in logs.items() block:
if nf.lower() in nf_union_lower:
    dag_nf_logs[nf] = [_truncate(e) for e in entries]
else:
    qualifying = [_truncate(e) for e in entries if _is_qualifying(e)]
```

4. Update the docstring — change the two lines that say "Messages are NEVER truncated" to:
```
Messages are truncated to cfg.rca_log_max_message_chars if they exceed that length.
```

### Step 4: Run tests

```bash
pytest tests/unit/test_logs_agent.py -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add src/triage_agent/agents/logs_agent.py tests/unit/test_logs_agent.py
git commit -m "fix: implement rca_log_max_message_chars truncation in compress_nf_logs"
```

---

## Task 5: Fix `compress_dag` zero-phases edge-case bug

**Why:** When all phases are filtered to empty in Step 3, `max(...)` over an all-empty list returns `[]`, `range(0, 0, -1)` is empty, the Step 4 loop never runs, and the function silently returns an over-budget result with all-empty phase lists. (Note: `compress_dag` is now in `utils.py` after Task 1.)

**Files:**
- Modify: `src/triage_agent/utils.py` (the `compress_dag` function)
- Modify: `tests/unit/test_utils.py` (test already written in Task 1 Step 1)

### Step 1: Verify the test written in Task 1 covers this bug

```bash
pytest tests/unit/test_utils.py::TestCompressDag::test_all_zero_phases_returns_stable_result -v
```

Expected: PASS (the guard was already added in Task 1).

If it fails (guard not yet present), add to `compress_dag` in `utils.py` before the Step 4 `for` loop:

```python
# Guard: if all DAGs have zero phases after step-3 filtering, further truncation is impossible
if not any(d.get("phases") for d in fp_only):
    return fp_only
```

### Step 2: Run all utils tests

```bash
pytest tests/unit/test_utils.py -v
```

Expected: all pass.

### Step 3: Commit (if any change was needed)

```bash
git add src/triage_agent/utils.py
git commit -m "fix: guard against zero-phases infinite skip in compress_dag step 4"
```

---

## Task 6: Remove stale `second_attempt_complete=False` fixture kwargs

**Why:** `second_attempt_complete` was removed from `TriageState` but two test fixtures still pass it as a kwarg. Python's TypedDict doesn't reject extra keys at runtime, so no test fails — but it's a maintenance hazard and triggers mypy under strict.

**Files:**
- Modify: `tests/conftest.py` (line 166)
- Modify: `tests/unit/test_dag_mapper.py` (line 165)

### Step 1: Verify the bad kwarg is present

```bash
grep -n "second_attempt_complete" tests/conftest.py tests/unit/test_dag_mapper.py
```

Expected: two matches.

### Step 2: Remove the kwarg from both fixtures

In `tests/conftest.py`, find and delete:
```python
        second_attempt_complete=False,
```

In `tests/unit/test_dag_mapper.py`, find and delete the same line.

### Step 3: Run tests to confirm nothing breaks

```bash
pytest tests/unit/test_dag_mapper.py tests/unit/test_graph.py -v
```

Expected: all pass.

### Step 4: Commit

```bash
git add tests/conftest.py tests/unit/test_dag_mapper.py
git commit -m "fix: remove stale second_attempt_complete kwarg from two test fixtures"
```

---

## Task 7: Replace per-call `ThreadPoolExecutor` with module-level singleton

**Why:** Each `save_artifact` call creates a new `ThreadPoolExecutor`, submits one task, and shuts it down. Under burst load (11+ artifact writes per incident), this spawns 11 executors per incident — unnecessary GC pressure.

**Fix:** Use a module-level executor with a small fixed pool.

**Files:**
- Modify: `src/triage_agent/utils.py`
- Modify: `tests/unit/test_utils.py`

### Step 1: Write failing test

In `tests/unit/test_utils.py`, add:

```python
import concurrent.futures
from triage_agent import utils as utils_module


class TestSaveArtifactSingleton:
    def test_same_executor_reused_across_calls(self, tmp_path: Any) -> None:
        """save_artifact reuses a single module-level executor, not a new one per call."""
        executor_before = utils_module._artifact_executor
        utils_module.save_artifact("inc1", "a.json", {"k": 1}, str(tmp_path))
        utils_module.save_artifact("inc1", "b.json", {"k": 2}, str(tmp_path))
        executor_after = utils_module._artifact_executor
        assert executor_before is executor_after

    def test_artifact_files_are_written(self, tmp_path: Any) -> None:
        """save_artifact actually writes the JSON file."""
        import time
        utils_module.save_artifact("inc2", "test.json", {"key": "value"}, str(tmp_path))
        # wait briefly for background thread
        time.sleep(0.1)
        artifact = tmp_path / "inc2" / "test.json"
        assert artifact.exists()
        import json
        assert json.loads(artifact.read_text())["key"] == "value"
```

### Step 2: Run to verify failure

```bash
pytest tests/unit/test_utils.py::TestSaveArtifactSingleton -v
```

Expected: `AttributeError: module 'triage_agent.utils' has no attribute '_artifact_executor'`

### Step 3: Replace in `utils.py`

Replace the entire `save_artifact` function (lines 65–75) and the `_write_artifact_sync` function with:

```python
# Module-level executor: avoids spawning a new thread per artifact write
_artifact_executor: concurrent.futures.ThreadPoolExecutor = (
    concurrent.futures.ThreadPoolExecutor(max_workers=2)
)


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

    Submits to a module-level ThreadPoolExecutor so no new thread is created per call.
    Failures are logged as warnings and silently swallowed.
    """
    _artifact_executor.submit(_write_artifact_sync, incident_id, name, data, artifacts_dir)
```

### Step 4: Run tests

```bash
pytest tests/unit/test_utils.py -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add src/triage_agent/utils.py tests/unit/test_utils.py
git commit -m "fix: replace per-call ThreadPoolExecutor with module-level singleton in save_artifact"
```

---

## Task 8: Add TTL eviction to `_incident_store`

**Why:** `_incident_store` is an unbounded in-memory dict — grows forever under continuous alerting.

**Fix:** Store each entry with a monotonic timestamp; evict entries older than a configurable TTL on each new `/webhook` POST. Add `incident_ttl_seconds` to config.

**Files:**
- Modify: `src/triage_agent/config.py`
- Modify: `src/triage_agent/api/webhook.py`
- Modify: `tests/unit/test_webhook.py`

### Step 1: Write failing test

In `tests/unit/test_webhook.py`, add:

```python
import time
from triage_agent.api import webhook as webhook_module


class TestIncidentStoreTTL:
    def test_stale_incidents_are_evicted(self) -> None:
        """Incidents older than the TTL are removed from the store on the next POST."""
        # Inject a stale entry directly
        webhook_module._incident_store["stale-id"] = {
            "ts": time.monotonic() - 9999,
            "data": {"layer": "application"},
        }
        assert "stale-id" in webhook_module._incident_store

        # Trigger eviction by calling _evict_stale directly
        webhook_module._evict_stale()
        assert "stale-id" not in webhook_module._incident_store

    def test_fresh_incidents_are_not_evicted(self) -> None:
        """Incidents added just now survive eviction."""
        webhook_module._incident_store["fresh-id"] = {
            "ts": time.monotonic(),
            "data": None,
        }
        webhook_module._evict_stale()
        assert "fresh-id" in webhook_module._incident_store
        # cleanup
        del webhook_module._incident_store["fresh-id"]
```

### Step 2: Run to verify failure

```bash
pytest tests/unit/test_webhook.py::TestIncidentStoreTTL -v
```

Expected: `AttributeError: module ... has no attribute '_evict_stale'`

### Step 3: Add `incident_ttl_seconds` to `config.py`

In `TriageAgentConfig`, add in the `api_config` section:

```python
# TTL for completed/failed incident entries in the in-memory store.
# Entries older than this are evicted on each new webhook POST.
incident_ttl_seconds: int = 3600
```

### Step 4: Rewrite `_incident_store` in `webhook.py`

Add `import time` at the top.

Replace:
```python
_incident_store: dict[str, dict[str, Any] | None] = {}
```
With:
```python
# Each value: {"ts": float (monotonic), "data": dict | None}
# data=None → pending; data=dict → complete or failed
_incident_store: dict[str, dict[str, Any]] = {}
```

Add eviction function:
```python
def _evict_stale() -> None:
    """Remove incident entries older than cfg.incident_ttl_seconds."""
    cutoff = time.monotonic() - _cfg.incident_ttl_seconds
    stale = [k for k, v in _incident_store.items() if v["ts"] < cutoff]
    for k in stale:
        del _incident_store[k]
```

Update `_run_triage` — replace the two `_incident_store[incident_id] = ...` lines:
```python
_incident_store[incident_id] = {"ts": time.monotonic(), "data": result.get("final_report") or {}}
# ...on except:
_incident_store[incident_id] = {"ts": time.monotonic(), "data": {"error": "triage_failed"}}
```

Update `receive_alert` — add eviction call before registering new incident:
```python
_evict_stale()
_incident_store[incident_id] = {"ts": time.monotonic(), "data": None}  # pending
```

Update `get_incident` — unwrap the new structure:
```python
entry = _incident_store.get(incident_id)
if entry is None:
    raise HTTPException(status_code=404, detail=f"Unknown incident_id: {incident_id}")
data = entry["data"]
if data is None:
    return IncidentResponse(incident_id=incident_id, status="pending")
if "error" in data:
    return IncidentResponse(incident_id=incident_id, status="failed", final_report=data)
return IncidentResponse(incident_id=incident_id, status="complete", final_report=data)
```

### Step 5: Run tests

```bash
pytest tests/unit/test_webhook.py -v
```

Expected: all pass.

### Step 6: Commit

```bash
git add src/triage_agent/config.py src/triage_agent/api/webhook.py tests/unit/test_webhook.py
git commit -m "fix: add TTL eviction to _incident_store to prevent unbounded memory growth"
```

---

## Task 9: Fix `mypy --strict` errors

**Why:** Two actionable regressions in this batch (`max_tokens` kwarg, wrong return type), plus 8 pre-existing `dict` type-arg errors in `state.py` and 4 unused/orphan ignore comments.

**Files:**
- Modify: `src/triage_agent/state.py`
- Modify: `src/triage_agent/agents/rca_agent.py`

### Step 1: Verify current error count

```bash
source .venv/bin/activate && mypy src/ --strict 2>&1 | grep "error:" | wc -l
```

Expected: 14

### Step 2: Fix `state.py` — add type params to bare `dict`

For each bare `dict` annotation in `TriageState`, replace with `dict[str, Any]` or the appropriate specific type. Add `from typing import Any` at the top if not present.

Exact replacements:
```python
# line 8
alert: dict[str, Any]
# line 13
infra_findings: dict[str, Any] | None
# line 18
dags: list[dict[str, Any]] | None
# line 24
metrics: dict[str, Any] | None
# line 25
logs: dict[str, Any] | None
# line 28
trace_deviations: dict[str, list[dict[str, Any]]] | None
# line 37
evidence_chain: list[dict[str, Any]]
# line 44
final_report: dict[str, Any] | None
# new field from Task 2:
compressed_evidence: dict[str, str] | None
```

Also add `from typing import Any` at line 3:
```python
from typing import Any, TypedDict
```

### Step 3: Fix `rca_agent.py` — `max_tokens` kwarg

`ChatOpenAI` in the installed version does not accept `max_tokens` as a constructor kwarg. Use `model_kwargs`:

In `create_llm`, for the `openai` provider, replace:
```python
max_tokens=4096,
```
With:
```python
model_kwargs={"max_tokens": 4096},
```

Do the same for the `local` provider block (same `ChatOpenAI`, same fix).

For the `anthropic` provider, `ChatAnthropic` does accept `max_tokens` — keep it.

### Step 4: Fix `rca_agent.py` — import-not-found for `langchain_anthropic`

Change line 321 from:
```python
from langchain_anthropic import ChatAnthropic  # deferred: optional dependency
```
To:
```python
from langchain_anthropic import ChatAnthropic  # type: ignore[import-not-found]  # optional dep
```

Remove the now-unnecessary `# type: ignore[return-value]` on line 327 and `# type: ignore[arg-type]` on line 329 (they were workarounds for the missing-import error).

### Step 5: Fix `rca_agent.py` — return type of `rca_agent_first_attempt`

Change the function signature from:
```python
def rca_agent_first_attempt(state: TriageState) -> TriageState:
```
To:
```python
def rca_agent_first_attempt(state: TriageState) -> dict[str, Any]:
```

### Step 6: Run mypy

```bash
mypy src/ --strict 2>&1 | grep "error:"
```

Expected: 0 errors.

### Step 7: Run all unit tests (regression check)

```bash
pytest tests/unit/ -v --tb=short
```

Expected: all pass.

### Step 8: Commit

```bash
git add src/triage_agent/state.py src/triage_agent/agents/rca_agent.py
git commit -m "fix: resolve all 14 mypy --strict errors (type params, max_tokens kwarg, return type)"
```

---

## Task 10: Resolve `artifacts_dir` to absolute path at config load

**Why:** A relative `artifacts_dir` is resolved from CWD, which differs between uvicorn invocations, Docker, and k8s. Resolving at config-load time makes the path predictable regardless of working directory.

**Files:**
- Modify: `src/triage_agent/config.py`
- Modify: `tests/unit/test_config.py`

### Step 1: Write failing test

In `tests/unit/test_config.py`, add:

```python
class TestArtifactsDirResolution:
    def test_relative_artifacts_dir_is_resolved_to_absolute(self) -> None:
        """A relative artifacts_dir must be converted to an absolute path at config load time."""
        from triage_agent.config import TriageAgentConfig
        cfg = TriageAgentConfig(artifacts_dir="artifacts")
        from pathlib import Path
        assert Path(cfg.artifacts_dir).is_absolute()

    def test_absolute_artifacts_dir_unchanged(self) -> None:
        from triage_agent.config import TriageAgentConfig
        from pathlib import Path
        cfg = TriageAgentConfig(artifacts_dir="/tmp/my_artifacts")
        assert cfg.artifacts_dir == "/tmp/my_artifacts"
```

### Step 2: Run to verify failure

```bash
pytest tests/unit/test_config.py::TestArtifactsDirResolution -v
```

Expected: FAIL (relative path returned as-is)

### Step 3: Add `field_validator` in `config.py`

After the existing `validate_url` validator, add:

```python
@field_validator("artifacts_dir")
@classmethod
def resolve_artifacts_dir(cls, v: str) -> str:
    """Resolve relative artifacts_dir to absolute path using CWD at config-load time."""
    from pathlib import Path  # noqa: PLC0415
    return str(Path(v).resolve())
```

### Step 4: Run tests

```bash
pytest tests/unit/test_config.py -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add src/triage_agent/config.py tests/unit/test_config.py
git commit -m "fix: resolve artifacts_dir to absolute path at config load time"
```

---

## Task 11: Fix `ruff` E501 line-too-long in `logs_agent.py`

**Why:** Lines 62 and 67 exceed the 120-char ruff limit because of long Loki label-selector f-strings.

**Files:**
- Modify: `src/triage_agent/agents/logs_agent.py`

### Step 1: Verify ruff errors

```bash
ruff check src/triage_agent/agents/logs_agent.py
```

Expected: two E501 errors on lines 62 and 67.

### Step 2: Extract label selector variable

In `build_loki_queries`, inside the `for nf in dag["all_nfs"]` loop, after `nf_lower = nf.lower()`, add:

```python
label_sel = f'{{k8s_namespace_name="{core_namespace}",k8s_pod_name=~".*{nf_lower}.*"}}'
```

Then replace lines 62 and 67:

Line 62 (was):
```python
f'{{k8s_namespace_name="{core_namespace}",k8s_pod_name=~".*{nf_lower}.*"}} |~ "{phase["success_log"]}"'
```
Replace with:
```python
f'{label_sel} |~ "{phase["success_log"]}"'
```

Line 67 (was):
```python
f'{{k8s_namespace_name="{core_namespace}",k8s_pod_name=~".*{nf_lower}.*"}} |~ "(?i){loki_pattern}"'
```
Replace with:
```python
f'{label_sel} |~ "(?i){loki_pattern}"'
```

Similarly apply the same `label_sel` refactor to the base query (line 55) for consistency.

### Step 3: Verify ruff passes

```bash
ruff check src/triage_agent/agents/logs_agent.py
```

Expected: 0 errors (the remaining ANN401 errors are in other files).

### Step 4: Run tests

```bash
pytest tests/unit/test_logs_agent.py -v
```

Expected: all pass.

### Step 5: Commit

```bash
git add src/triage_agent/agents/logs_agent.py
git commit -m "fix: extract label_sel variable to fix E501 in build_loki_queries"
```

---

## Task 12: Update architecture docs

**Why:** Docs were updated in `df47d8e` to reflect `join_for_rca`. The HEAD commit then removed it from the graph without updating docs. After Task 2, `join_for_rca` is back as a real barrier node owned by `rca_agent.py`.

**Files:**
- Modify: `docs/triageagent_architecture_design2.md`
- Modify: `docs/workflow_diagram.mermaid`
- Modify: `docs/state_flow_diagram.mermaid`

### Step 1: Update `workflow_diagram.mermaid`

The pipeline flow should read:
```
START → infra_agent → join_for_rca
START → dag_mapper → metrics_agent → evidence_quality → join_for_rca
                   → logs_agent   → evidence_quality
                   → traces_agent → evidence_quality
join_for_rca → rca_agent → [conditional] → finalize | increment_attempt → rca_agent
```

Ensure the mermaid diagram node for `join_for_rca` has a label: `join_for_rca["join_for_rca\n(compress evidence)"]`

### Step 2: Update `state_flow_diagram.mermaid`

Add the `compressed_evidence` state field in the state diagram. It should appear between `evidence_quality_score` and `root_nf`, labelled as written by `join_for_rca` and read by `rca_agent`.

### Step 3: Update `triageagent_architecture_design2.md`

Find and update:
1. The pipeline diagram — add `join_for_rca` as the converge node between `[InfraAgent, EvidenceQuality]` and `RCAAgent`.
2. Any text describing the topology — replace "infra_agent → END superstep merge" language with "infra_agent → join_for_rca (explicit barrier)".
3. The component description section — update `join_for_rca` entry to describe it as: "Evidence-joining barrier node in `rca_agent.py`. Waits for both `infra_agent` and `evidence_quality` before compressing all evidence for the LLM context window. Owns the `compressed_evidence` state field."
4. The state fields table — add `compressed_evidence: dict[str, str] | None`.

### Step 4: Verify docs render (optional)

```bash
# Preview mermaid in terminal if mermaid-js CLI is installed
# mmdc -i docs/workflow_diagram.mermaid -o /tmp/workflow.svg 2>&1 || echo "mermaid CLI not installed — skip"
```

### Step 5: Commit

```bash
git add docs/triageagent_architecture_design2.md docs/workflow_diagram.mermaid docs/state_flow_diagram.mermaid
git commit -m "docs: update architecture to reflect join_for_rca as evidence-joining barrier"
```

---

## Final Verification

```bash
# All unit tests pass
pytest tests/unit/ -v --tb=short

# mypy clean
mypy src/ --strict 2>&1 | grep "error:" | wc -l   # expect: 0

# ruff clean (E501 resolved; only ANN401 remain for pre-existing Any usages)
ruff check src/ 2>&1 | grep "^E" | wc -l           # expect: 0

# Full commit log for this batch
git log --oneline -12
```

Expected test count: all 533+ unit tests pass.
