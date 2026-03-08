# Parallel Agent Execution with Multi-Procedure DAG Mapping — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the sequential `metrics→logs→traces` pipeline with a parallel fan-out from a new `dag_mapper` node, while adding multi-procedure DAG support.

**Architecture:** A new `dag_mapper` agent runs from `START` in parallel with `infra_agent`. It maps the alert to one or more 3GPP procedure DAGs from Memgraph, computes an NF union, then fans out to `metrics_agent`, `logs_agent`, and `traces_agent` simultaneously. All three converge at `evidence_quality` before `rca_agent`.

**Tech Stack:** LangGraph (`StateGraph`, `add_edge`), LangSmith (`@traceable`), Memgraph (Bolt/Neo4j driver), pytest + `unittest.mock.patch`

---

## Context you must read first

Before starting, read these files to understand existing patterns:

- `src/triage_agent/state.py` — shared state TypedDict
- `src/triage_agent/graph.py` — LangGraph workflow wiring
- `src/triage_agent/agents/metrics_agent.py` — already returns delta dict (parallel-safe pattern)
- `src/triage_agent/agents/logs_agent.py` — returns full state (must be fixed)
- `src/triage_agent/agents/ue_traces_agent.py` — returns full state (must be fixed)
- `src/triage_agent/memgraph/connection.py` — `load_reference_dag(dag_name)` method
- `tests/conftest.py` — shared fixtures (`sample_alert`, `sample_dag`, `sample_initial_state`)
- `tests/unit/test_evidence_quality.py` — example of existing test style

**LangGraph parallel-merge rule:** When multiple nodes all write to the same state keys concurrently, LangGraph raises a merge conflict. Safe agents return only a delta dict containing the keys they write — never the full state. `infra_agent` and `metrics_agent` already do this correctly; `logs_agent` and `ue_traces_agent` do not.

**Run tests with:**
```bash
cd /home/agent/workspace/5g-triage-agent
source .venv/bin/activate
pytest tests/unit/ -v
```

---

## Task 1: Update `state.py` — multi-procedure field shapes

Three fields change from singular to list; one new field is added.

**Files:**
- Modify: `src/triage_agent/state.py`
- Modify: `tests/conftest.py` (fixture must match new schema)
- Modify: `src/triage_agent/graph.py` (`get_initial_state` must match new schema)

### Step 1: Write the failing test

Add to `tests/unit/test_state.py` (create if it doesn't exist — check first):

```python
def test_state_has_plural_dag_fields() -> None:
    """State schema uses list fields for multi-procedure support."""
    from triage_agent.state import TriageState
    import inspect

    hints = TriageState.__annotations__
    assert hints["procedure_names"] == "list[str] | None"
    assert hints["dag_ids"] == "list[str] | None"
    assert hints["dags"] == "list[dict] | None"
    assert hints["nf_union"] == "list[str] | None"
    # Old singular fields must be gone
    assert "procedure_name" not in hints
    assert "dag_id" not in hints
    assert "dag" not in hints
```

### Step 2: Run to verify it fails

```bash
pytest tests/unit/test_state.py::test_state_has_plural_dag_fields -v
```

Expected: `FAILED` — `KeyError` or `AssertionError` because old field names still exist.

### Step 3: Update `state.py`

Replace the DAG mapping section (lines 16–19) with:

```python
# DAG mapping outputs (alert → one or more 3GPP procedures)
procedure_names: list[str] | None  # e.g. ["registration_general", "authentication_5g_aka"]
dag_ids: list[str] | None
dags: list[dict] | None            # Full DAG structures from Memgraph, one per procedure
nf_union: list[str] | None         # Deduplicated union of all_nfs across matched DAGs
mapping_confidence: float          # Overall mapping quality (0.0-1.0)
mapping_method: str                # "exact_match"|"keyword_match"|"nf_default"|"generic_fallback"
```

Remove the old fields: `procedure_name`, `dag_id`, `dag`.

### Step 4: Update `tests/conftest.py`

In `sample_initial_state` fixture, replace the three old fields with the four new ones:

```python
# Old (remove these three lines):
procedure_name=None,
dag_id=None,
dag=None,

# New (add these four lines):
procedure_names=None,
dag_ids=None,
dags=None,
nf_union=None,
```

Also rename `sample_dag` fixture → `sample_dags` and make it return a list:

```python
@pytest.fixture
def sample_dags() -> list[dict[str, Any]]:
    """Sample DAG list — one registration procedure DAG."""
    return [
        {
            "name": "registration_general",
            "spec": "TS 23.502 4.2.2.2.2",
            "procedure": "registration",
            "all_nfs": ["AMF", "AUSF", "UDM", "NRF", "PCF"],
            "phases": [
                {
                    "order": 1,
                    "nf": "UE",
                    "action": "Registration Request",
                    "keywords": ["Registration Request", "Initial Registration", "SUCI"],
                    "optional": False,
                },
                {
                    "order": 9,
                    "nf": "AMF",
                    "action": "Authentication/Security",
                    "keywords": ["Authentication", "Security", "AUSF", "AKA"],
                    "sub_dag": "authentication_5g_aka",
                    "optional": False,
                    "failure_patterns": ["*auth*fail*", "*timeout*AUSF*"],
                },
                {
                    "order": 21,
                    "nf": "AMF",
                    "action": "Registration Accept",
                    "keywords": ["Registration Accept"],
                    "optional": False,
                    "success_log": "Registration Accept sent",
                    "failure_patterns": ["*registration*reject*", "*accept*fail*"],
                },
            ],
        }
    ]
```

### Step 5: Update `graph.py` — `get_initial_state`

Replace the DAG mapping lines in `get_initial_state`:

```python
# Old (remove):
procedure_name=None,
dag_id=None,
dag=None,
mapping_confidence=0.0,
mapping_method="",

# New (add):
procedure_names=None,
dag_ids=None,
dags=None,
nf_union=None,
mapping_confidence=0.0,
mapping_method="",
```

### Step 6: Run tests — fix any breakage from field renames

```bash
pytest tests/unit/ -v
```

Some tests that access `state["dag"]` or `state["procedure_name"]` will now fail. Fix them by updating references to the new field names. Do NOT change agent logic yet — that happens in later tasks.

### Step 7: Commit

```bash
git add src/triage_agent/state.py src/triage_agent/graph.py tests/conftest.py tests/unit/test_state.py
git commit -m "feat: update state schema for multi-procedure DAG support"
```

---

## Task 2: New `dag_mapper` agent

This deterministic agent (no LLM) maps an alert to one or more 3GPP procedures and fetches their DAGs from Memgraph.

**Files:**
- Create: `src/triage_agent/agents/dag_mapper.py`
- Create: `tests/unit/test_dag_mapper.py`

### Mapping cascade (priority order)

| Method | Trigger | Confidence |
|--------|---------|-----------|
| `exact_match` | Alert label `procedure` directly names a known DAG | 1.0 |
| `keyword_match` | `alertname` or `description` contains a procedure keyword | 0.8 |
| `nf_default` | Alert's `nf` label maps to known procedure(s) | 0.6 |
| `generic_fallback` | No match above — return all known procedures | 0.3 |

Known DAG names (must match names used in Memgraph Cypher scripts):

```python
KNOWN_DAGS: list[str] = [
    "registration_general",
    "authentication_5g_aka",
    "pdu_session_establishment",
]

KEYWORD_MAP: dict[str, list[str]] = {
    "registration": ["registration_general"],
    "auth": ["authentication_5g_aka", "registration_general"],
    "pdu": ["pdu_session_establishment"],
    "session": ["pdu_session_establishment"],
}

NF_DEFAULT_MAP: dict[str, list[str]] = {
    "amf":  ["registration_general", "authentication_5g_aka"],
    "ausf": ["authentication_5g_aka"],
    "udm":  ["registration_general", "authentication_5g_aka"],
    "smf":  ["pdu_session_establishment"],
    "upf":  ["pdu_session_establishment"],
    "nrf":  ["registration_general"],
    "pcf":  ["registration_general"],
    "udr":  ["registration_general"],
    "nssf": ["registration_general"],
}
```

### Step 1: Write failing tests

Create `tests/unit/test_dag_mapper.py`:

```python
"""Tests for DagMapper alert-to-procedure mapping."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from triage_agent.state import TriageState


# --- Pure function tests (no Memgraph) ---

class TestMapAlertToProcedures:
    """Tests for the alert→procedure mapping cascade."""

    def test_exact_match_via_procedure_label(self) -> None:
        """Alert label 'procedure' that names a known DAG → exact_match."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"procedure": "registration_general", "nf": "amf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert dag_names == ["registration_general"]
        assert method == "exact_match"
        assert confidence == pytest.approx(1.0)

    def test_keyword_match_registration_in_alertname(self) -> None:
        """'registration' in alertname → keyword_match for registration_general."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": ""},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert "registration_general" in dag_names
        assert method == "keyword_match"
        assert confidence == pytest.approx(0.8)

    def test_keyword_match_auth_in_description(self) -> None:
        """'auth' in description → keyword_match for authentication_5g_aka."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "NfDown"},
            "annotations": {"description": "AUSF authentication timeout"},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert "authentication_5g_aka" in dag_names
        assert method == "keyword_match"

    def test_nf_default_amf(self) -> None:
        """AMF alert with no keywords → nf_default returns registration + auth."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "AmfDown", "nf": "amf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert set(dag_names) == {"registration_general", "authentication_5g_aka"}
        assert method == "nf_default"
        assert confidence == pytest.approx(0.6)

    def test_nf_default_smf(self) -> None:
        """SMF alert → nf_default returns pdu_session_establishment only."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "SmfError", "nf": "smf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert dag_names == ["pdu_session_establishment"]
        assert method == "nf_default"

    def test_generic_fallback_unknown_nf_no_keywords(self) -> None:
        """Alert with unrecognised NF and no keywords → generic_fallback."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "SomeGenericAlert", "nf": "unknown-nf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert set(dag_names) == {"registration_general", "authentication_5g_aka", "pdu_session_establishment"}
        assert method == "generic_fallback"
        assert confidence == pytest.approx(0.3)

    def test_exact_match_unknown_procedure_label_falls_through(self) -> None:
        """A 'procedure' label that is NOT a known DAG name falls through to next method."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": "amf", "procedure": "unknown_dag"},
            "annotations": {},
        }
        _, method, _ = map_alert_to_procedures(alert)

        assert method != "exact_match"


class TestComputeNfUnion:
    """Tests for NF union computation across multiple DAGs."""

    def test_union_deduplicates_nfs(self) -> None:
        """NFs appearing in multiple DAGs appear only once in the union."""
        from triage_agent.agents.dag_mapper import compute_nf_union

        dags = [
            {"all_nfs": ["AMF", "AUSF", "UDM"]},
            {"all_nfs": ["AMF", "SMF", "UPF"]},
        ]
        union = compute_nf_union(dags)

        assert sorted(union) == sorted(["AMF", "AUSF", "UDM", "SMF", "UPF"])

    def test_empty_dags_returns_empty_list(self) -> None:
        """No DAGs → empty NF union."""
        from triage_agent.agents.dag_mapper import compute_nf_union

        assert compute_nf_union([]) == []


# --- Agent entry point tests (Memgraph mocked) ---

class TestDagMapperAgent:
    """Tests for the dag_mapper agent entry point."""

    def _make_state(self, alert: dict[str, Any]) -> TriageState:
        return TriageState(
            alert=alert,
            incident_id="test-001",
            infra_checked=False,
            infra_score=0.0,
            infra_findings=None,
            procedure_names=None,
            dag_ids=None,
            dags=None,
            nf_union=None,
            mapping_confidence=0.0,
            mapping_method="",
            metrics=None,
            logs=None,
            discovered_imsis=None,
            traces_ready=False,
            trace_deviations=None,
            evidence_quality_score=0.0,
            root_nf=None,
            failure_mode=None,
            layer="",
            confidence=0.0,
            evidence_chain=[],
            degraded_mode=False,
            degraded_reason=None,
            attempt_count=1,
            max_attempts=2,
            needs_more_evidence=False,
            evidence_gaps=None,
            second_attempt_complete=False,
            final_report=None,
        )

    def test_returns_delta_dict_with_all_required_keys(self, mock_memgraph: MagicMock) -> None:
        """dag_mapper returns a delta dict with procedure_names, dags, nf_union, etc."""
        from triage_agent.agents.dag_mapper import dag_mapper

        mock_memgraph.load_reference_dag.return_value = {
            "name": "registration_general",
            "procedure": "registration",
            "all_nfs": ["AMF", "AUSF"],
            "phases": [],
        }

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": "amf"},
            "annotations": {},
            "startsAt": "2026-02-15T10:00:00Z",
        }

        with patch("triage_agent.agents.dag_mapper.get_memgraph", return_value=mock_memgraph):
            result = dag_mapper(self._make_state(alert))

        required_keys = {"procedure_names", "dag_ids", "dags", "nf_union", "mapping_confidence", "mapping_method"}
        assert required_keys.issubset(result.keys())

    def test_memgraph_failure_returns_empty_dags(self, mock_memgraph: MagicMock) -> None:
        """If Memgraph raises, dag_mapper returns empty dags (degraded mode)."""
        from triage_agent.agents.dag_mapper import dag_mapper

        mock_memgraph.load_reference_dag.side_effect = Exception("Memgraph unavailable")

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": "amf"},
            "annotations": {},
            "startsAt": "2026-02-15T10:00:00Z",
        }

        with patch("triage_agent.agents.dag_mapper.get_memgraph", return_value=mock_memgraph):
            result = dag_mapper(self._make_state(alert))

        assert result["dags"] == []
        assert result["nf_union"] == []

    def test_nf_union_is_deduplicated_across_matched_dags(self, mock_memgraph: MagicMock) -> None:
        """When multiple DAGs are matched, nf_union contains no duplicates."""
        from triage_agent.agents.dag_mapper import dag_mapper

        def load_dag(name: str) -> dict[str, Any]:
            return {
                "name": name,
                "procedure": name,
                "all_nfs": ["AMF", "AUSF"] if "registration" in name else ["AMF", "SMF"],
                "phases": [],
            }

        mock_memgraph.load_reference_dag.side_effect = load_dag

        alert = {
            "labels": {"alertname": "AmfDown", "nf": "amf"},
            "annotations": {},
            "startsAt": "2026-02-15T10:00:00Z",
        }

        with patch("triage_agent.agents.dag_mapper.get_memgraph", return_value=mock_memgraph):
            result = dag_mapper(self._make_state(alert))

        assert len(result["nf_union"]) == len(set(result["nf_union"]))
```

### Step 2: Run to verify tests fail

```bash
pytest tests/unit/test_dag_mapper.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'triage_agent.agents.dag_mapper'`

### Step 3: Implement `dag_mapper.py`

Create `src/triage_agent/agents/dag_mapper.py`:

```python
"""DagMapper: Maps alert to one or more 3GPP procedure DAGs.

No LLM. Uses a priority cascade to map alert labels/annotations to known
3GPP procedures, then fetches each procedure's reference DAG from Memgraph.

Mapping cascade:
    1. exact_match   — alert label 'procedure' names a known DAG directly
    2. keyword_match — alertname or description contains a procedure keyword
    3. nf_default    — alert's 'nf' label maps to known default procedures
    4. generic_fallback — all known procedures (low confidence)
"""

import logging
from typing import Any

from langsmith import traceable

from triage_agent.memgraph.connection import get_memgraph
from triage_agent.state import TriageState

logger = logging.getLogger(__name__)

KNOWN_DAGS: list[str] = [
    "registration_general",
    "authentication_5g_aka",
    "pdu_session_establishment",
]

KEYWORD_MAP: dict[str, list[str]] = {
    "registration": ["registration_general"],
    "auth": ["authentication_5g_aka", "registration_general"],
    "pdu": ["pdu_session_establishment"],
    "session": ["pdu_session_establishment"],
}

NF_DEFAULT_MAP: dict[str, list[str]] = {
    "amf":  ["registration_general", "authentication_5g_aka"],
    "ausf": ["authentication_5g_aka"],
    "udm":  ["registration_general", "authentication_5g_aka"],
    "smf":  ["pdu_session_establishment"],
    "upf":  ["pdu_session_establishment"],
    "nrf":  ["registration_general"],
    "pcf":  ["registration_general"],
    "udr":  ["registration_general"],
    "nssf": ["registration_general"],
}


def map_alert_to_procedures(
    alert: dict[str, Any],
) -> tuple[list[str], str, float]:
    """Map alert to a list of DAG names using the priority cascade.

    Returns:
        (dag_names, mapping_method, mapping_confidence)
    """
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    # 1. exact_match
    procedure_label = labels.get("procedure", "").strip().lower()
    if procedure_label in KNOWN_DAGS:
        return [procedure_label], "exact_match", 1.0

    # 2. keyword_match — scan alertname + description
    search_text = " ".join([
        labels.get("alertname", ""),
        annotations.get("description", ""),
        annotations.get("summary", ""),
    ]).lower()

    matched: list[str] = []
    for keyword, dag_names in KEYWORD_MAP.items():
        if keyword in search_text:
            for name in dag_names:
                if name not in matched:
                    matched.append(name)

    if matched:
        return matched, "keyword_match", 0.8

    # 3. nf_default — use NF label
    nf_label = labels.get("nf", "").strip().lower()
    if nf_label in NF_DEFAULT_MAP:
        return NF_DEFAULT_MAP[nf_label], "nf_default", 0.6

    # 4. generic_fallback
    return KNOWN_DAGS, "generic_fallback", 0.3


def compute_nf_union(dags: list[dict[str, Any]]) -> list[str]:
    """Compute deduplicated union of all_nfs across a list of DAGs."""
    seen: set[str] = set()
    result: list[str] = []
    for dag in dags:
        for nf in dag.get("all_nfs", []):
            if nf not in seen:
                seen.add(nf)
                result.append(nf)
    return result


@traceable(name="DagMapper")
def dag_mapper(state: TriageState) -> dict[str, Any]:
    """DagMapper entry point. Deterministic, no LLM.

    Maps alert to procedure DAGs and computes NF union for downstream agents.
    Gracefully degrades (empty dags) if Memgraph is unreachable.
    """
    alert = state["alert"]
    dag_names, method, confidence = map_alert_to_procedures(alert)

    dags: list[dict[str, Any]] = []
    loaded_dag_ids: list[str] = []

    try:
        conn = get_memgraph()
        for name in dag_names:
            dag = conn.load_reference_dag(name)
            if dag is not None:
                dags.append(dag)
                loaded_dag_ids.append(name)
            else:
                logger.warning("DAG not found in Memgraph: %s", name)
    except Exception:
        logger.warning(
            "Memgraph unavailable in dag_mapper, proceeding with empty DAGs",
            exc_info=True,
        )
        dags = []
        loaded_dag_ids = []

    return {
        "procedure_names": loaded_dag_ids,
        "dag_ids": loaded_dag_ids,
        "dags": dags,
        "nf_union": compute_nf_union(dags),
        "mapping_confidence": confidence,
        "mapping_method": method,
    }
```

### Step 4: Run tests

```bash
pytest tests/unit/test_dag_mapper.py -v
```

Expected: All tests PASS.

### Step 5: Commit

```bash
git add src/triage_agent/agents/dag_mapper.py tests/unit/test_dag_mapper.py
git commit -m "feat: add dag_mapper agent with multi-procedure support"
```

---

## Task 3: Update `metrics_agent.py` — use `nf_union`

**Files:**
- Modify: `src/triage_agent/agents/metrics_agent.py`
- Modify: `tests/unit/test_metrics_agent.py`

### Step 1: Read existing tests

Read `tests/unit/test_metrics_agent.py` before making any changes. Tests that set `state["dag"]` need updating to use `state["nf_union"]` instead.

### Step 2: Write failing test

Add to `tests/unit/test_metrics_agent.py`:

```python
def test_metrics_agent_uses_nf_union_not_dag(
    self, sample_initial_state: TriageState
) -> None:
    """metrics_agent reads nf_union from state, not state['dag']."""
    from triage_agent.agents.metrics_agent import metrics_agent

    state = sample_initial_state
    state["nf_union"] = ["AMF", "AUSF"]
    state["dags"] = None  # dag is now absent

    with patch("triage_agent.agents.metrics_agent.asyncio.run", return_value=[]):
        result = metrics_agent(state)

    assert "metrics" in result

def test_metrics_agent_empty_nf_union_returns_empty(
    self, sample_initial_state: TriageState
) -> None:
    """metrics_agent with empty nf_union returns {'metrics': {}}."""
    from triage_agent.agents.metrics_agent import metrics_agent

    state = sample_initial_state
    state["nf_union"] = []

    result = metrics_agent(state)

    assert result == {"metrics": {}}
```

### Step 3: Run to verify failure

```bash
pytest tests/unit/test_metrics_agent.py -v -k "nf_union"
```

Expected: `FAILED` — either `KeyError: 'nf_union'` or the test that checks `dags` is absent fails.

### Step 4: Update `metrics_agent.py`

In `metrics_agent` function, replace:

```python
# Old
dag = state["dag"]
if dag is None:
    return {"metrics": {}}
...
queries = build_nf_queries(dag["all_nfs"])
...
return {"metrics": organize_metrics_by_nf(raw_results, dag["all_nfs"])}
```

With:

```python
# New
nf_union = state.get("nf_union") or []
if not nf_union:
    return {"metrics": {}}
...
queries = build_nf_queries(nf_union)
...
return {"metrics": organize_metrics_by_nf(raw_results, nf_union)}
```

### Step 5: Update existing tests in `test_metrics_agent.py`

Any test that sets `state["dag"] = sample_dag` must be updated to set:
```python
state["nf_union"] = sample_dags[0]["all_nfs"]  # use sample_dags fixture
```

Update the fixture parameter from `sample_dag` → `sample_dags` in those test signatures.

### Step 6: Run all metrics tests

```bash
pytest tests/unit/test_metrics_agent.py -v
```

Expected: All PASS.

### Step 7: Commit

```bash
git add src/triage_agent/agents/metrics_agent.py tests/unit/test_metrics_agent.py
git commit -m "feat: metrics_agent reads nf_union instead of dag"
```

---

## Task 4: Update `logs_agent.py` — multi-dag phases + delta return

Two changes: (1) build queries from the union of phases across all DAGs, (2) return a delta dict instead of the full state.

**Files:**
- Modify: `src/triage_agent/agents/logs_agent.py`
- Modify: `tests/unit/test_logs_agent.py`

### Step 1: Read existing tests

Read `tests/unit/test_logs_agent.py` before making changes.

### Step 2: Write failing tests

Add to `tests/unit/test_logs_agent.py`:

```python
def test_logs_agent_returns_delta_dict_not_full_state(
    self, sample_initial_state: TriageState, sample_dags: list[dict]
) -> None:
    """logs_agent must return only {'logs': ...}, not the full TriageState."""
    from triage_agent.agents.logs_agent import logs_agent

    state = sample_initial_state
    state["dags"] = sample_dags
    state["nf_union"] = sample_dags[0]["all_nfs"]

    with patch("triage_agent.agents.logs_agent.asyncio.run", return_value=[]):
        result = logs_agent(state)

    # Delta dict must contain exactly 'logs' — not state-wide keys
    assert set(result.keys()) == {"logs"}

def test_logs_agent_builds_queries_from_all_dags_phases(
    self, sample_initial_state: TriageState
) -> None:
    """build_loki_queries_from_dags unions phases from all matched DAGs."""
    from triage_agent.agents.logs_agent import build_loki_queries_from_dags

    dags = [
        {
            "all_nfs": ["AMF"],
            "phases": [{"actors": ["AMF"], "success_log": "ok", "failure_patterns": ["*fail*"]}],
        },
        {
            "all_nfs": ["SMF"],
            "phases": [{"actors": ["SMF"], "success_log": "done", "failure_patterns": ["*error*"]}],
        },
    ]
    queries = build_loki_queries_from_dags(dags, "5g-core")

    # Queries must cover both AMF (from dag 1) and SMF (from dag 2)
    all_queries = " ".join(queries)
    assert "amf" in all_queries
    assert "smf" in all_queries

def test_logs_agent_empty_dags_returns_empty_logs(
    self, sample_initial_state: TriageState
) -> None:
    """logs_agent with empty dags returns {'logs': {}}."""
    from triage_agent.agents.logs_agent import logs_agent

    state = sample_initial_state
    state["dags"] = []

    result = logs_agent(state)

    assert result == {"logs": {}}
```

### Step 3: Run to verify failure

```bash
pytest tests/unit/test_logs_agent.py -v -k "delta or dags or empty_dags"
```

Expected: FAILED — `AssertionError` because `logs_agent` still returns full state and `build_loki_queries_from_dags` doesn't exist yet.

### Step 4: Update `logs_agent.py`

**4a.** Add a new helper `build_loki_queries_from_dags` that replaces `build_loki_queries`:

```python
def build_loki_queries_from_dags(
    dags: list[dict[str, Any]], core_namespace: str
) -> list[str]:
    """Build LogQL queries from the union of NFs and phases across all matched DAGs."""
    # Collect unique NFs across all dags
    all_nfs: list[str] = []
    seen_nfs: set[str] = set()
    all_phases: list[dict[str, Any]] = []
    for dag in dags:
        for nf in dag.get("all_nfs", []):
            if nf not in seen_nfs:
                seen_nfs.add(nf)
                all_nfs.append(nf)
        all_phases.extend(dag.get("phases", []))

    # Reuse existing build_loki_queries with a synthetic combined dag
    combined = {"all_nfs": all_nfs, "phases": all_phases}
    return build_loki_queries(combined, core_namespace)
```

**4b.** Update `logs_agent` function:

```python
@traceable(name="NfLogsAgent")
def logs_agent(state: TriageState) -> dict[str, Any]:
    """NfLogsAgent entry point. Pure MCP/HTTP query, no LLM."""
    dags = state.get("dags") or []
    if not dags:
        return {"logs": {}}

    cfg = get_config()
    alert_time = parse_timestamp(state["alert"]["startsAt"])
    start = int(alert_time - cfg.alert_lookback_seconds)
    end = int(alert_time + cfg.alert_lookahead_seconds)

    queries = build_loki_queries_from_dags(dags, cfg.core_namespace)

    logs_raw: list[dict[str, Any]] = []
    if queries:
        try:
            use_mcp = asyncio.run(_check_mcp_available())
        except Exception:
            logger.warning("MCP health check failed, defaulting to direct Loki", exc_info=True)
            use_mcp = False

        if use_mcp:
            try:
                logs_raw = asyncio.run(_fetch_loki_logs(queries, start=start, end=end))
            except Exception:
                logger.warning("MCP queries failed, proceeding with empty logs", exc_info=True)
        else:
            logger.info("MCP server unavailable, using direct Loki connection")
            try:
                logs_raw = asyncio.run(_fetch_loki_logs_direct(queries, start=start, end=end))
            except Exception:
                logger.warning("Direct Loki query failed, proceeding with empty logs", exc_info=True)

    # Build combined dag for annotation (union of all phases)
    combined_dag: dict[str, Any] = {
        "all_nfs": [],
        "phases": [p for dag in dags for p in dag.get("phases", [])],
    }
    return {"logs": organize_and_annotate_logs(logs_raw, combined_dag)}
```

**4c.** Update existing tests in `test_logs_agent.py` that set `state["dag"]` to instead set `state["dags"]`.

### Step 5: Run all logs tests

```bash
pytest tests/unit/test_logs_agent.py -v
```

Expected: All PASS.

### Step 6: Commit

```bash
git add src/triage_agent/agents/logs_agent.py tests/unit/test_logs_agent.py
git commit -m "feat: logs_agent uses multi-dag phases and returns delta dict"
```

---

## Task 5: Update `ue_traces_agent.py` — per-procedure deviation + delta return

Two changes: (1) run Memgraph deviation detection against each DAG separately, (2) return a delta dict.

**Files:**
- Modify: `src/triage_agent/agents/ue_traces_agent.py`
- Modify: `tests/unit/test_ue_traces_agent.py`

### Step 1: Read existing tests

Read `tests/unit/test_ue_traces_agent.py` before making changes.

### Step 2: Write failing tests

Add to `tests/unit/test_ue_traces_agent.py`:

```python
def test_discover_and_trace_imsis_returns_delta_dict(
    self, sample_initial_state: TriageState, mock_memgraph: MagicMock
) -> None:
    """discover_and_trace_imsis returns delta dict, not full state."""
    from triage_agent.agents.ue_traces_agent import discover_and_trace_imsis

    state = sample_initial_state
    state["dags"] = [{"name": "registration_general", "all_nfs": ["AMF"]}]

    with (
        patch("triage_agent.agents.ue_traces_agent.loki_query", return_value=[]),
        patch("triage_agent.agents.ue_traces_agent.get_memgraph", return_value=mock_memgraph),
    ):
        result = discover_and_trace_imsis(state)

    expected_keys = {"discovered_imsis", "traces_ready", "trace_deviations"}
    assert set(result.keys()) == expected_keys

def test_deviation_detection_runs_per_procedure(
    self, sample_initial_state: TriageState, mock_memgraph: MagicMock
) -> None:
    """trace_deviations is keyed by procedure name, one entry per matched DAG."""
    from triage_agent.agents.ue_traces_agent import discover_and_trace_imsis

    state = sample_initial_state
    state["dags"] = [
        {"name": "registration_general", "all_nfs": ["AMF"]},
        {"name": "authentication_5g_aka", "all_nfs": ["AUSF"]},
    ]
    mock_memgraph.execute_cypher.return_value = []  # no IMSIs found

    with (
        patch("triage_agent.agents.ue_traces_agent.loki_query", return_value=[]),
        patch("triage_agent.agents.ue_traces_agent.get_memgraph", return_value=mock_memgraph),
    ):
        result = discover_and_trace_imsis(state)

    deviations = result["trace_deviations"]
    assert isinstance(deviations, dict)
    assert "registration_general" in deviations
    assert "authentication_5g_aka" in deviations

def test_empty_dags_returns_empty_traces(
    self, sample_initial_state: TriageState
) -> None:
    """With no DAGs, traces agent returns minimal empty delta dict."""
    from triage_agent.agents.ue_traces_agent import discover_and_trace_imsis

    state = sample_initial_state
    state["dags"] = []

    result = discover_and_trace_imsis(state)

    assert result["discovered_imsis"] == []
    assert result["traces_ready"] is False
    assert result["trace_deviations"] == {}
```

### Step 3: Run to verify failure

```bash
pytest tests/unit/test_ue_traces_agent.py -v -k "delta or per_procedure or empty_dags"
```

Expected: FAILED — returns full state and `trace_deviations` is not a dict keyed by name.

### Step 4: Update `ue_traces_agent.py`

Update `run_deviation_detection` to accept a dag name and return deviations for that dag, and update `discover_and_trace_imsis`:

```python
def run_deviation_detection_for_dag(
    incident_id: str, dag_name: str
) -> list[dict[str, Any]]:
    """Compare ingested traces against a single reference DAG in Memgraph."""
    conn = get_memgraph()
    imsi_records = conn.execute_cypher(
        "MATCH (t:CapturedTrace {incident_id: $incident_id}) RETURN t.imsi AS imsi",
        {"incident_id": incident_id},
    )
    deviations: list[dict[str, Any]] = []
    for record in imsi_records:
        imsi = record["imsi"]
        deviation = conn.detect_deviation(incident_id, imsi, dag_name)
        if deviation is not None:
            deviations.append(deviation)
    return deviations


@traceable(name="UeTracesAgent")
def discover_and_trace_imsis(state: TriageState) -> dict[str, Any]:
    """UeTracesAgent entry point. Pure MCP query + Memgraph, no LLM."""
    dags = state.get("dags") or []
    if not dags:
        return {"discovered_imsis": [], "traces_ready": False, "trace_deviations": {}}

    cfg = get_config()
    alert_time = int(parse_timestamp(state["alert"]["startsAt"]))

    # 1. Discovery query
    discovery_logql = f'{{k8s_namespace_name="{cfg.core_namespace}"}} |~ "(?i)imsi-"'
    discovery_logs = loki_query(
        discovery_logql,
        start=alert_time - cfg.imsi_discovery_window_seconds,
        end=alert_time + cfg.imsi_discovery_window_seconds,
    )
    imsis = extract_unique_imsis(discovery_logs)

    # 2. Per-IMSI trace construction
    traces: list[dict[str, Any]] = []
    for imsi in imsis:
        logql = per_imsi_logql(imsi)
        raw_trace = loki_query(
            logql,
            start=alert_time - cfg.imsi_trace_lookback_seconds,
            end=alert_time + cfg.alert_lookahead_seconds,
        )
        traces.append(contract_imsi_trace(raw_trace, imsi))

    # 3. Ingest into Memgraph
    ingest_traces_to_memgraph(traces, state["incident_id"])

    # 4. Per-procedure deviation detection
    trace_deviations: dict[str, list[dict[str, Any]]] = {}
    for dag in dags:
        dag_name = dag.get("name", "")
        if dag_name:
            trace_deviations[dag_name] = run_deviation_detection_for_dag(
                state["incident_id"], dag_name
            )

    return {
        "discovered_imsis": imsis,
        "traces_ready": True,
        "trace_deviations": trace_deviations,
    }
```

**Note:** The old `run_deviation_detection` function can be kept for backwards compatibility or removed if no other code uses it — check with `grep -r "run_deviation_detection" src/`.

### Step 5: Update existing tests

Tests that set `state["dag"]` or `state["procedure_name"]` must be updated:
- `state["dag"] = sample_dag` → `state["dags"] = sample_dags`
- `state["procedure_name"] = "registration"` → `state["procedure_names"] = ["registration_general"]`

Tests that assert on `result["discovered_imsis"]` where `result` was the full state must now assert on `result["discovered_imsis"]` from the delta dict (no change to assertion, but the fixture setup changes).

### Step 6: Run all UeTraces tests

```bash
pytest tests/unit/test_ue_traces_agent.py -v
```

Expected: All PASS.

### Step 7: Commit

```bash
git add src/triage_agent/agents/ue_traces_agent.py tests/unit/test_ue_traces_agent.py
git commit -m "feat: ue_traces_agent runs per-procedure deviation detection and returns delta dict"
```

---

## Task 6: Update `evidence_quality.py` — delta return

Minor: change return from full state to delta dict for consistency with parallel-safe pattern.

**Files:**
- Modify: `src/triage_agent/agents/evidence_quality.py`
- Modify: `tests/unit/test_evidence_quality.py` (tests already assert on delta — verify they still pass)

### Step 1: Verify existing tests already expect delta dict

The existing tests do `result["evidence_quality_score"]` — this currently works because `result` IS the full state (a dict), but the key happens to exist. After this change it will still work, only now `result` contains only `{"evidence_quality_score": ...}`.

Run existing tests to establish a baseline:

```bash
pytest tests/unit/test_evidence_quality.py -v
```

Expected: All PASS (baseline).

### Step 2: Write a test that would fail if full state is returned

Add to `tests/unit/test_evidence_quality.py`:

```python
def test_returns_only_delta_dict(
    self, sample_initial_state: TriageState
) -> None:
    """compute_evidence_quality returns only {'evidence_quality_score': float}."""
    state = sample_initial_state
    state["metrics"] = {"AMF": []}
    state["logs"] = None
    state["traces_ready"] = False

    result = compute_evidence_quality(state)

    assert set(result.keys()) == {"evidence_quality_score"}
```

Run it:

```bash
pytest tests/unit/test_evidence_quality.py::TestComputeEvidenceQuality::test_returns_only_delta_dict -v
```

Expected: FAILED — currently returns full state with many keys.

### Step 3: Update `evidence_quality.py`

Replace:

```python
state["evidence_quality_score"] = min(quality_score, 1.0)
return state
```

With:

```python
return {"evidence_quality_score": min(quality_score, 1.0)}
```

### Step 4: Run all evidence quality tests

```bash
pytest tests/unit/test_evidence_quality.py -v
```

Expected: All PASS.

### Step 5: Commit

```bash
git add src/triage_agent/agents/evidence_quality.py tests/unit/test_evidence_quality.py
git commit -m "feat: evidence_quality returns delta dict for parallel safety"
```

---

## Task 7: Rewire `graph.py` + update `test_graph.py`

Replace the sequential `metrics→logs→traces` chain with a parallel fan-out from `dag_mapper`.

**Files:**
- Modify: `src/triage_agent/graph.py`
- Modify: `tests/unit/test_graph.py`

### Step 1: Write failing tests

Add to `tests/unit/test_graph.py`:

```python
def test_dag_mapper_fans_out_to_all_three_agents(self) -> None:
    """dag_mapper has edges to metrics_agent, logs_agent, and traces_agent."""
    graph = create_workflow().get_graph()
    dag_mapper_targets = {e.target for e in graph.edges if e.source == "dag_mapper"}

    assert "metrics_agent" in dag_mapper_targets
    assert "logs_agent" in dag_mapper_targets
    assert "traces_agent" in dag_mapper_targets

def test_dag_mapper_starts_from_start(self) -> None:
    """dag_mapper has an edge from __start__."""
    graph = create_workflow().get_graph()
    edge_pairs = [(e.source, e.target) for e in graph.edges]

    assert ("__start__", "dag_mapper") in edge_pairs

def test_all_three_agents_converge_at_evidence_quality(self) -> None:
    """metrics_agent, logs_agent, and traces_agent all have edges to evidence_quality."""
    graph = create_workflow().get_graph()
    edge_pairs = [(e.source, e.target) for e in graph.edges]

    assert ("metrics_agent", "evidence_quality") in edge_pairs
    assert ("logs_agent", "evidence_quality") in edge_pairs
    assert ("traces_agent", "evidence_quality") in edge_pairs

def test_no_sequential_edges_between_collection_agents(self) -> None:
    """There must be no sequential edges: metrics→logs, logs→traces."""
    graph = create_workflow().get_graph()
    edge_pairs = [(e.source, e.target) for e in graph.edges]

    assert ("metrics_agent", "logs_agent") not in edge_pairs
    assert ("logs_agent", "traces_agent") not in edge_pairs
```

### Step 2: Run to verify failure

```bash
pytest tests/unit/test_graph.py -v -k "dag_mapper or converge or sequential"
```

Expected: FAILED — `dag_mapper` node doesn't exist in the graph yet.

### Step 3: Update `graph.py`

**3a.** Add the import:

```python
from triage_agent.agents.dag_mapper import dag_mapper
```

**3b.** Add the node inside `create_workflow()`:

```python
workflow.add_node("dag_mapper", dag_mapper)
```

**3c.** Replace the edge section — remove old sequential chain and add new parallel topology:

```python
# --- Old edges to REMOVE ---
# workflow.add_edge(START, "metrics_agent")      ← remove
# workflow.add_edge("metrics_agent", "logs_agent")  ← remove
# workflow.add_edge("logs_agent", "traces_agent")   ← remove
# workflow.add_edge("traces_agent", "evidence_quality")  ← remove

# --- New edges ---
# Parallel start: InfraAgent and DagMapper both run from START
workflow.add_edge(START, "infra_agent")
workflow.add_edge(START, "dag_mapper")

# DagMapper fans out to all three collection agents (parallel)
workflow.add_edge("dag_mapper", "metrics_agent")
workflow.add_edge("dag_mapper", "logs_agent")
workflow.add_edge("dag_mapper", "traces_agent")

# All three converge at evidence_quality
workflow.add_edge("metrics_agent", "evidence_quality")
workflow.add_edge("logs_agent", "evidence_quality")
workflow.add_edge("traces_agent", "evidence_quality")

# Both branches (infra + evidence) must complete before RCA
workflow.add_edge("infra_agent", "rca_agent")
workflow.add_edge("evidence_quality", "rca_agent")
```

**3d.** Update the existing test `test_parallel_edges_for_infra_agent_and_metrics_agent`:

```python
def test_parallel_edges_from_start(self) -> None:
    """Both infra_agent and dag_mapper have edges from START (parallel execution)."""
    graph = create_workflow().get_graph()
    edge_pairs = [(e.source, e.target) for e in graph.edges]

    assert ("__start__", "infra_agent") in edge_pairs
    assert ("__start__", "dag_mapper") in edge_pairs
```

### Step 4: Run all graph tests

```bash
pytest tests/unit/test_graph.py -v
```

Expected: All PASS.

### Step 5: Run full unit test suite

```bash
pytest tests/unit/ -v
```

Expected: All PASS. Fix any remaining failures from field renames before committing.

### Step 6: Commit

```bash
git add src/triage_agent/graph.py tests/unit/test_graph.py
git commit -m "feat: rewire graph for parallel dag_mapper fan-out to metrics/logs/traces agents"
```

---

## Final verification

```bash
# Full unit suite
pytest tests/unit/ -v

# Confirm graph topology is correct
python -c "
from triage_agent.graph import create_workflow
g = create_workflow().get_graph()
print('Edges:')
for e in g.edges:
    print(f'  {e.source} → {e.target}')
"

# Draw ASCII graph
python -c "from triage_agent.graph import create_workflow; print(create_workflow().get_graph().draw_ascii())"

# Type check all modified files
mypy src/triage_agent/state.py \
     src/triage_agent/graph.py \
     src/triage_agent/agents/dag_mapper.py \
     src/triage_agent/agents/metrics_agent.py \
     src/triage_agent/agents/logs_agent.py \
     src/triage_agent/agents/ue_traces_agent.py \
     src/triage_agent/agents/evidence_quality.py \
     --strict

# Lint
ruff check src/triage_agent/
```
