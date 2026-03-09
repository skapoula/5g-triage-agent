# Agent Development Guide

How to write a new agent for the 5G TriageAgent pipeline. Read this before touching `graph.py`.

## The agent contract

An agent is a plain Python function:

```python
def my_agent(state: TriageState) -> dict[str, Any]:
    ...
    return {"field_i_own": value}
```

- Takes the full `TriageState` as input (read anything you need)
- Returns **only the fields you write** — LangGraph merges the delta
- Never mutate `state` in-place; always return a new dict
- Never return the full state

## Minimal template

```python
"""MyAgent: one sentence what this does."""

import logging
from typing import Any

from langsmith import traceable

from triage_agent.config import get_config
from triage_agent.state import TriageState

logger = logging.getLogger(__name__)


@traceable(name="MyAgent")
def my_agent(state: TriageState) -> dict[str, Any]:
    """What this agent does and what it produces."""
    cfg = get_config()

    # Read from state — always use .get() with a sensible default
    nf_union = state.get("nf_union") or []
    incident_id = state.get("incident_id", "unknown")

    # ... do work ...

    return {
        "my_field": result,
    }
```

Key points:
- `@traceable(name="MyAgent")` — required for LangSmith span visibility
- `get_config()` — always use this; never hardcode thresholds
- `state.get("field")` not `state["field"]` — state fields may not be populated if upstream failed
- Return only owned fields

## Reading state safely

```python
# Good — guards against None
nf_union = state.get("nf_union") or []
dags = state.get("dags") or []

# Good — specific default
infra_score = state.get("infra_score", 0.0)

# Bad — will raise KeyError if upstream agent didn't run
nf_union = state["nf_union"]  # don't do this (except in join_for_rca / rca_agent which explicitly require it)
```

## Using MCP (Prometheus / Loki)

`MCPClient` is defined in `src/triage_agent/mcp/client.py`. All params default to config
values if omitted.

```python
import asyncio
from triage_agent.mcp.client import MCPClient

def my_agent(state: TriageState) -> dict[str, Any]:
    cfg = get_config()
    client = MCPClient(
        prometheus_url=cfg.prometheus_url,  # optional; defaults to config
        loki_url=cfg.loki_url,
        timeout=cfg.mcp_timeout,
    )

    # Prometheus instant query
    result = asyncio.run(client.query_prometheus(
        query='up{namespace="5g-core"}',
    ))

    # Prometheus range query — start/end are Unix timestamps (int)
    result = asyncio.run(client.query_prometheus_range(
        query=f'rate(http_requests_total{{nf="{nf}"}}[1m])',
        start=start_ts,
        end=end_ts,
        step=cfg.promql_range_step,  # optional; defaults to config
    ))

    # Loki log query — start/end are Unix timestamps (int); client converts to nanoseconds
    logs = asyncio.run(client.query_loki(
        logql=f'{{namespace="5g-core", pod=~".*{nf}.*"}}',
        start=start_ts,
        end=end_ts,
        limit=cfg.loki_query_limit,  # optional; defaults to config
    ))

    return {"my_result": result}
```

If your agent function is itself `async`, call the client methods with `await` directly
instead of `asyncio.run()`:
```python
async def my_async_agent(state: TriageState) -> dict[str, Any]:
    client = MCPClient()
    result = await client.query_prometheus('up{namespace="5g-core"}')
    return {"my_result": result}
```

## Using Memgraph

`get_memgraph()` in `src/triage_agent/memgraph/connection.py` returns a singleton
`MemgraphConnection`. Two methods:
- `execute_cypher(query, params)` — read queries, returns `list[dict[str, Any]]`
- `execute_cypher_write(query, params)` — write queries, returns `None`

Both retry on `ServiceUnavailable`/`TransientError` up to `memgraph_max_retries` times
with exponential backoff (`2^attempt` seconds).

```python
from triage_agent.memgraph.connection import get_memgraph

def my_agent(state: TriageState) -> dict[str, Any]:
    memgraph = get_memgraph()

    # Read query
    rows = memgraph.execute_cypher(
        "MATCH (t:ReferenceTrace {name: $name})-[:STEP]->(e:RefEvent) "
        "RETURN e ORDER BY e.order",
        {"name": "Registration_General"},
    )

    # Write query (e.g. ingesting trace data)
    memgraph.execute_cypher_write(
        "CREATE (t:CapturedTrace {incident_id: $incident_id, imsi: $imsi})",
        {"incident_id": state.get("incident_id"), "imsi": "123456789012345"},
    )

    return {"phases": rows}
```

## Saving artifacts (optional)

`save_artifact()` in `src/triage_agent/utils.py` writes a JSON snapshot for debugging.
Fire-and-forget — non-blocking, never raises (logs warnings on failure).

```python
from triage_agent.utils import save_artifact

save_artifact(
    incident_id=state.get("incident_id", "unknown"),
    name="my_agent_output.json",
    data={"key": "value"},
    artifacts_dir=cfg.artifacts_dir,
)
```

Artifacts land at `artifacts_dir/<incident_id>/my_agent_output.json`.

## Adding a new node to the graph

Four steps:

**1. Write the agent** in `src/triage_agent/agents/my_agent.py` following the template above.

**2. Add new state fields** to `src/triage_agent/state.py`:
```python
class TriageState(TypedDict):
    # ... existing fields ...
    my_field: dict[str, Any] | None
```

**3. Register the node and wire edges** in `src/triage_agent/graph.py`:
```python
from triage_agent.agents.my_agent import my_agent

workflow.add_node("my_agent", my_agent)
workflow.add_edge("dag_mapper", "my_agent")       # example: runs after dag_mapper
workflow.add_edge("my_agent", "evidence_quality") # example: feeds into evidence_quality
```

**4. Initialise the field** in `get_initial_state()` in `graph.py`:
```python
return TriageState(
    # ... existing fields ...
    my_field=None,
)
```

### Important: adding an agent to the barrier

If your new agent runs in parallel with the existing collection agents and its output needs to
reach the RCA prompt, you must also:
- Add an edge from your agent to `evidence_quality` (so it converges at the barrier)
- Update `compress_evidence()` in `rca_agent.py` to include your output in `compressed_evidence`
- Update `RCA_PROMPT_TEMPLATE` to include the new evidence section

## Testing patterns

### Unit test structure

```python
# tests/unit/test_my_agent.py
from triage_agent.agents.my_agent import my_agent
from triage_agent.state import TriageState


def test_my_agent_returns_expected_field(sample_initial_state: TriageState) -> None:
    """my_agent writes my_field when nf_union is populated."""
    sample_initial_state["nf_union"] = ["AMF", "AUSF"]

    result = my_agent(sample_initial_state)

    assert "my_field" in result
    assert result["my_field"] is not None


def test_my_agent_handles_empty_nf_union(sample_initial_state: TriageState) -> None:
    """my_agent returns empty result when nf_union is None."""
    sample_initial_state["nf_union"] = None

    result = my_agent(sample_initial_state)

    assert result["my_field"] == {}
```

The `sample_initial_state` fixture is defined in `tests/conftest.py` and provides a fully
populated `TriageState` suitable for unit testing.

### Mocking MCP and Memgraph

```python
from unittest.mock import patch


def test_my_agent_calls_prometheus(
    sample_initial_state: TriageState,
) -> None:
    """my_agent queries Prometheus with the correct PromQL."""
    mock_response = {"result": []}

    with patch("triage_agent.agents.my_agent.MCPClient") as mock_cls:
        mock_client = mock_cls.return_value
        # query_prometheus is async — use AsyncMock if your agent calls it with await
        mock_client.query_prometheus.return_value = mock_response

        result = my_agent(sample_initial_state)

    mock_client.query_prometheus.assert_called_once()
    assert "my_field" in result
```

For async methods called via `asyncio.run()`, you may need `unittest.mock.AsyncMock`:
```python
from unittest.mock import AsyncMock, patch

mock_client.query_prometheus = AsyncMock(return_value=mock_response)
```

### Run tests

```bash
pytest tests/unit/test_my_agent.py -v
mypy src/triage_agent/agents/my_agent.py --strict
ruff check src/triage_agent/agents/my_agent.py
```

## Common mistakes

| Mistake | Why it breaks | Fix |
|---------|---------------|-----|
| `state["nf_union"]` (hard access) | Raises `KeyError` if DagMapper failed | Use `state.get("nf_union") or []` |
| Calling LLM outside `rca_agent.py` | Breaks the "only RCA uses LLM" contract | Feed data into `compressed_evidence` and let RCAAgent decide |
| Blocking I/O in async function (`requests.get`) | Blocks the event loop | Use `httpx.AsyncClient` or `await asyncio.to_thread(...)` |
| Missing `@traceable` | Agent invisible in LangSmith | Always add `@traceable(name="MyAgent")` |
| Returning the full state | Creates deep copy, causes type errors | Return only the dict of fields you write |
| Not initialising field in `get_initial_state()` | `TypedDict` validation error at startup | Add field to `get_initial_state()` with `None` or empty default |
| Using `query_loki(query=...)` | Wrong param name | The parameter is `logql=`, not `query=` |
| Passing nanoseconds to `query_loki` | Double-conversion: client already multiplies by 1e9 | Pass Unix timestamps (seconds), not nanoseconds |
