# Claude Code Development Guide
## 5G TriageAgent v3.2 - Multi-Agent LangGraph Implementation

| Document | Version | Date | Status |
|----------|---------|------|--------|
| Claude Code Development Guide | 1.0 | February 2026 | Implementation Ready |

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Claude Code Configuration](#2-claude-code-configuration)
3. [Development Workflow](#3-development-workflow)
4. [Phase 1: Core Infrastructure](#4-phase-1-core-infrastructure)
5. [Phase 2: Agent Implementation](#5-phase-2-agent-implementation)
6. [Phase 3: LangGraph Orchestration](#6-phase-3-langgraph-orchestration)
7. [Phase 4: Integration & Testing](#7-phase-4-integration--testing)
8. [Subagent Definitions](#8-subagent-definitions)
9. [Common Patterns & Anti-Patterns](#9-common-patterns--anti-patterns)
10. [Troubleshooting Guide](#10-troubleshooting-guide)

---

## 1. Project Overview

### 1.1 System Architecture

```
InfraAgent (parallel) → NfMetricsAgent + NfLogsAgent + UeTracesAgent (parallel) → EvidenceQuality → RCAAgent
```

**Key Design Decisions**:
- **5 specialized agents**: 4 deterministic (no LLM), 1 uses LLM (RCAAgent only)
- **Shared state object**: `TriageState` TypedDict flows through the entire pipeline
- **MCP protocol**: Standardized interface to Prometheus, Loki, Kubernetes APIs
- **Memgraph**: In-memory graph DB for 3GPP reference DAGs and IMSI trace comparison

### 1.2 Technology Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Orchestration | LangGraph | Directed graph workflow with parallel execution |
| Observability | LangSmith | Tracing, feedback loops, confidence calibration |
| Data Sources | Prometheus/Loki via MCP | 3s timeout per query |
| Graph DB | Memgraph | Bolt protocol (port 7687), Cypher queries |
| LLM | LangChain + Configurable Provider | Only used by RCAAgent |
| API | FastAPI | Webhook endpoint on port 8000 |

### 1.3 Project Structure

```
5g-triage-agent/
├── CLAUDE.md                    # Claude Code memory file (updated below)
├── pyproject.toml               # Dependencies and build config
├── src/
│   └── triage_agent/
│       ├── __init__.py
│       ├── config.py            # Pydantic Settings
│       ├── state.py             # TriageState definition
│       ├── graph.py             # LangGraph workflow definition
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── infra_agent.py
│       │   ├── metrics_agent.py
│       │   ├── logs_agent.py
│       │   ├── ue_traces_agent.py
│       │   ├── evidence_quality.py
│       │   └── rca_agent.py
│       ├── mcp/
│       │   ├── __init__.py
│       │   ├── client.py        # MCP client wrapper
│       │   └── queries.py       # PromQL/LogQL query builders
│       ├── memgraph/
│       │   ├── __init__.py
│       │   ├── connection.py    # Bolt driver setup
│       │   └── deviation.py     # DAG comparison logic
│       └── api/
│           ├── __init__.py
│           └── webhook.py       # FastAPI alertmanager webhook
├── dags/
│   ├── authentication_5g_aka.cypher
│   ├── registration_general.cypher
│   └── pdu_session_establishment.cypher
├── k8s/
│   ├── deployment.yaml
│   ├── deployment-with-init.yaml
│   └── alertmanager-webhook.yaml
├── scripts/
│   └── trace_ue_v3.sh
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── .claude/
    ├── agents/                  # Custom subagents for this project
    │   ├── memgraph-expert.md
    │   ├── promql-builder.md
    │   └── 5g-protocol-reviewer.md
    └── settings.local.json      # Project-specific Claude Code settings
```

---

## 2. Claude Code Configuration

### 2.1 Enhanced CLAUDE.md

Replace the existing `CLAUDE.md` with this expanded version:

```markdown
# CLAUDE.md

## Project: 5G TriageAgent v3.2

### What This Is
A multi-agent LangGraph orchestration system for real-time root cause analysis of 5G core network failures. When Prometheus Alertmanager fires an alert, the system coordinates specialized agents to localize failures across infrastructure, NF, and 3GPP procedure layers.

### Architecture
See `triageagent_architecture_design2.md` for full architecture. The pipeline is:
```
InfraAgent (parallel) → NfMetricsAgent + NfLogsAgent + UeTracesAgent (parallel) → EvidenceQuality → RCAAgent
```

### Tech Stack
- **Orchestration**: LangGraph (directed graph workflow)
- **Observability**: LangSmith (tracing, feedback)
- **Data Sources**: Prometheus (metrics), Loki (logs) via MCP protocol
- **Graph DB**: Memgraph (Bolt protocol on port 7687, Cypher queries) — stores 3GPP reference DAGs and IMSI traces
- **LLM**: Used only by RCAAgent for analysis. All other agents are deterministic.
- **API**: FastAPI webhook endpoint on port 8000

### Key Conventions

#### State Management
- All agents read/write to a shared `TriageState` TypedDict (see `src/triage_agent/state.py`)
- Never modify state outside of agent functions
- Use LangGraph's `Send` for parallel execution

#### Database
- Memgraph, NOT Redis. Bolt protocol, port 7687, `mgconsole` CLI, Cypher queries.
- DAG definitions are Cypher scripts in `dags/` — loaded via init container
- Neo4j Python driver is used for Memgraph (compatible Bolt protocol)

#### Deployment
- Container configs are in `k8s/` — do not embed YAML in Python code
- Memgraph runs as sidecar container, not separate service
- Init container loads DAGs before main app starts

#### 5G Protocol
- The auth procedure is **5G AKA** (TS 33.501 Fig 6.1.3.2), NOT EAP-AKA'
- Reference DAGs from: TS 23.502 (procedures), TS 33.501 (security)
- NF names: AMF, SMF, UPF, NRF, AUSF, UDM, UDR, PCF, NSSF

#### Code Style
- Type hints required on all functions
- Use `@traceable` decorator from langsmith for agent functions
- Async functions preferred for MCP calls
- No LLM calls except in rca_agent.py

### Running Tests
```bash
pytest tests/unit/ -v
pytest tests/integration/ --memgraph-url bolt://localhost:7687
pytest tests/e2e/ --alert-webhook http://localhost:8000/webhook
```

### Building
```bash
pip install -e ".[dev]"

# Run locally
uvicorn triage_agent.api.webhook:app --reload --port 8000

# Load DAGs into Memgraph
mgconsole < dags/registration_general.cypher
mgconsole < dags/authentication_5g_aka.cypher
mgconsole < dags/pdu_session_establishment.cypher
```

### Task Verification Commands
```bash
# Check Memgraph connectivity
mgconsole -host localhost -port 7687 <<< "MATCH (n) RETURN count(n);"

# Test Prometheus MCP
curl -s http://prometheus:9090/api/v1/query?query=up | jq '.data.result'

# Test Loki MCP
curl -s 'http://loki:3100/loki/api/v1/labels' | jq '.data'

# Run LangGraph workflow locally
python -c "from triage_agent.graph import create_workflow; print(create_workflow().get_graph().draw_ascii())"
```

### Common Mistakes to Avoid
1. **Don't use Redis** — this project uses Memgraph for graph storage
2. **Don't add LLM calls to non-RCA agents** — only RCAAgent uses LLM
3. **Don't hardcode PromQL in agent functions** — use INFRA_PROMETHEUS_QUERIES constants
4. **Don't forget @traceable decorator** — required for LangSmith observability
5. **Don't use blocking I/O in async functions** — use httpx, not requests
```

### 2.2 Project-Specific Settings

Create `.claude/settings.local.json`:

```json
{
  "env": {
    "MEMGRAPH_HOST": "localhost",
    "MEMGRAPH_PORT": "7687",
    "PROMETHEUS_URL": "http://localhost:9090",
    "LOKI_URL": "http://localhost:3100",
    "LANGSMITH_PROJECT": "5g-triage-agent-v3"
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "ruff check --fix $CLAUDE_FILE_PATH 2>/dev/null || true"
          }
        ]
      }
    ]
  },
  "permissions": {
    "allow": [
      "Bash(pytest*)",
      "Bash(mgconsole*)",
      "Bash(curl*prometheus*)",
      "Bash(curl*loki*)",
      "Bash(pip install*)",
      "Bash(uvicorn*)",
      "Bash(ruff*)"
    ],
    "deny": [
      "Bash(rm -rf /)",
      "Bash(kubectl delete*)"
    ]
  }
}
```

---

## 3. Development Workflow

### 3.1 Test-First Development (TDD) - MANDATORY

**Claude Code MUST follow this test-first workflow for all code changes:**

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: WRITE TESTS FIRST (No Implementation Yet)          │
│                                                             │
│  claude "Write pytest tests for XyzAgent covering:          │
│    - Normal operation with valid inputs                     │
│    - Edge cases (empty data, missing fields)                │
│    - Error handling (timeouts, connection failures)         │
│    - Integration points (MCP calls, state updates)          │
│    DO NOT implement the code yet."                          │
│                                                             │
│  Output: tests/unit/test_xyz_agent.py                       │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: REVIEW TESTS                                       │
│                                                             │
│  - Verify test cases match requirements                     │
│  - Check edge cases are covered                             │
│  - Ensure mocks are appropriate                             │
│  - Confirm assertions are meaningful                        │
│                                                             │
│  Human reviews and approves tests before proceeding.        │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 3: IMPLEMENT TO PASS TESTS                            │
│                                                             │
│  claude "Implement XyzAgent to pass these tests:            │
│    [paste approved test file content]                       │
│    Follow patterns from existing agents.                    │
│    Use @traceable decorator for LangSmith."                 │
│                                                             │
│  Output: src/triage_agent/agents/xyz_agent.py               │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 4: VERIFY (All Three Must Pass)                       │
│                                                             │
│  # Run tests                                                │
│  pytest tests/unit/test_xyz_agent.py -v                     │
│                                                             │
│  # Type checking                                            │
│  mypy src/triage_agent/agents/xyz_agent.py --strict         │
│                                                             │
│  # Linting                                                  │
│  ruff check src/triage_agent/agents/xyz_agent.py            │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 5: COMMIT                                             │
│                                                             │
│  git add tests/unit/test_xyz_agent.py                       │
│  git add src/triage_agent/agents/xyz_agent.py               │
│  git commit -m "feat(xyz-agent): implement with tests"      │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Test-First Prompt Templates

**Template for Step 1 (Write Tests First):**
```
Write pytest tests for {ModuleName} that will be located at {test_path}.

MODULE PURPOSE:
{description of what the module does}

INTERFACES:
- Input: {input types and sources}
- Output: {output types and destinations}
- Dependencies: {MCP client, Memgraph, etc.}

TEST CASES REQUIRED:
1. Happy path with valid inputs
2. Edge case: empty/missing data
3. Edge case: malformed inputs  
4. Error handling: timeout
5. Error handling: connection failure
6. State updates are correct

MOCKING REQUIREMENTS:
- Mock {list external dependencies}
- Use pytest fixtures from conftest.py

DO NOT IMPLEMENT THE MODULE YET. Only write the tests.
```

**Template for Step 3 (Implement to Pass Tests):**
```
Implement {ModuleName} at {source_path} to pass these tests:

```python
{paste complete test file content}
```

REQUIREMENTS:
- All tests must pass
- Use @traceable decorator for LangSmith observability
- Follow patterns from {reference existing module}
- Type hints on all functions
- Docstrings on public functions

VERIFICATION (I will run these after):
pytest {test_path} -v
mypy {source_path} --strict
ruff check {source_path}
```

### 3.3 Example: InfraAgent Test-First Workflow

**Step 1 Prompt:**
```
Write pytest tests for InfraAgent at tests/unit/test_infra_agent.py.

MODULE PURPOSE:
Rule-based infrastructure triage. Queries Prometheus via MCP for pod metrics,
computes a weighted infrastructure score (0.0-1.0), updates TriageState.

INTERFACES:
- Input: TriageState with alert payload
- Output: TriageState with infra_score, infra_findings
- Dependencies: MCPClient for Prometheus queries

TEST CASES REQUIRED:
1. compute_infrastructure_score with all healthy metrics → score near 0.0
2. compute_infrastructure_score with pod restarts → score reflects weight
3. compute_infrastructure_score with OOM kill → score reflects critical weight
4. compute_infrastructure_score with resource saturation → score reflects weight
5. infra_agent updates state correctly
6. infra_agent handles MCP timeout gracefully
7. infra_agent handles partial metric failures

WEIGHT TABLE FOR SCORING:
| Factor | Weight | Scoring |
| Restarts | 0.35 | 0:0.0, 1-2:0.4, 3-5:0.7, >5:1.0 |
| OOM | 0.25 | 0:0.0, >0:1.0 |
| Pod Status | 0.20 | Running:0.0, Pending:0.6, Failed:1.0 |
| Resources | 0.20 | Mem>90%:1.0, CPU>1.0:0.8, Normal:0.0 |

DO NOT IMPLEMENT infra_agent.py YET. Only write the tests.
```

**Step 3 Prompt (after test approval):**
```
Implement InfraAgent at src/triage_agent/agents/infra_agent.py to pass these tests:

```python
{paste approved test_infra_agent.py content}
```

REQUIREMENTS:
- All tests must pass
- Use @traceable decorator
- Async function using MCPClient
- Follow the INFRA_PROMETHEUS_QUERIES constant pattern

VERIFICATION:
pytest tests/unit/test_infra_agent.py -v
mypy src/triage_agent/agents/infra_agent.py --strict
ruff check src/triage_agent/agents/infra_agent.py
```

### 3.2 Session Management Strategy

| Task Type | Session Strategy | Context Management |
|-----------|------------------|-------------------|
| New module implementation | Fresh session | Load CLAUDE.md + relevant existing files |
| Bug fix | Fresh session | Load failing test + stack trace + relevant code |
| Refactoring | Fresh session | Load module + tests |
| Code review | Subagent | Isolated context, read-only |
| Documentation | Same session | Accumulate context from implementation |

**When to `/clear`**:
- After completing a module before starting the next
- After 2 failed correction attempts
- When switching between unrelated tasks
- When context approaches 80% full

### 3.3 Verification Commands

Include these in every implementation prompt:

```bash
# Unit tests for the specific module
pytest tests/unit/test_<module>.py -v

# Type checking
mypy src/triage_agent/<module>.py --strict

# Lint
ruff check src/triage_agent/<module>.py

# Integration test (if applicable)
pytest tests/integration/test_<module>_integration.py -v
```

---

## 4. Phase 1: Core Infrastructure

### 4.1 Task 1.1: Configuration Module

**Prompt**:
```
Create src/triage_agent/config.py with:

1. Pydantic Settings class `TriageAgentConfig` loading from environment:
   - MEMGRAPH_HOST (default: "localhost")
   - MEMGRAPH_PORT (default: 7687)
   - PROMETHEUS_URL (default: "http://prometheus:9090")
   - LOKI_URL (default: "http://loki:3100")
   - LLM_API_KEY (required, no default)
   - LLM_MODEL (default: "gpt-4o-mini")
   - LLM_TIMEOUT (default: 30)
   - LANGSMITH_PROJECT (default: "5g-triage-agent")
   - MCP_TIMEOUT (default: 3.0)

2. Validation:
   - LLM_API_KEY must be set (raise ValueError if missing)
   - MEMGRAPH_PORT must be positive integer
   - URLs must start with http:// or https://

3. Singleton pattern for config access:
   - get_config() function returns cached instance

Include tests in tests/unit/test_config.py covering:
- Default values
- Environment variable override
- Validation errors
- Singleton behavior
```

**Expected Output** (`src/triage_agent/config.py`):
```python
"""Configuration management for TriageAgent."""

from functools import lru_cache
from pydantic_settings import BaseSettings


class TriageAgentConfig(BaseSettings):
    """Configuration loaded from environment variables."""
    
    # Memgraph
    memgraph_host: str = "localhost"
    memgraph_port: int = 7687
    
    # MCP Server URLs
    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    mcp_timeout: float = 3.0
    
    # LLM Configuration
    llm_api_key: str  # Required, no default
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 30
    
    # Observability
    langsmith_project: str = "5g-triage-agent"
    
    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
    }
    
    @property
    def memgraph_uri(self) -> str:
        """Bolt connection URI for Memgraph."""
        return f"bolt://{self.memgraph_host}:{self.memgraph_port}"


@lru_cache(maxsize=1)
def get_config() -> TriageAgentConfig:
    """Get singleton configuration instance."""
    return TriageAgentConfig()
```

### 4.2 Task 1.2: Memgraph Connection Module

**Prompt**:
```
Create src/triage_agent/memgraph/connection.py with:

1. Class `MemgraphConnection`:
   - Uses neo4j Python driver (compatible with Memgraph)
   - Connection pooling with max 10 connections
   - Automatic retry on transient failures (3 attempts, exponential backoff)
   - Context manager support

2. Methods:
   - execute_cypher(query: str, params: dict = None) -> list[dict]
   - execute_cypher_write(query: str, params: dict = None) -> None
   - health_check() -> bool

3. Singleton pattern:
   - get_memgraph() function returns cached connection

Reference: The project uses Memgraph, NOT Redis. Connection is via Bolt protocol.
Config comes from get_config().memgraph_uri

Include tests in tests/unit/test_memgraph_connection.py with mocked driver.
```

**Expected Output** (`src/triage_agent/memgraph/connection.py`):
```python
"""Memgraph connection management via Bolt protocol."""

from functools import lru_cache
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError

from triage_agent.config import get_config


class MemgraphConnection:
    """Memgraph connection with pooling and retry logic."""
    
    def __init__(self, uri: str, max_connection_pool_size: int = 10):
        self._driver = GraphDatabase.driver(
            uri,
            max_connection_pool_size=max_connection_pool_size,
        )
    
    def execute_cypher(
        self, 
        query: str, 
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        """Execute a read-only Cypher query with retry logic."""
        last_error = None
        for attempt in range(max_retries):
            try:
                with self._driver.session() as session:
                    result = session.run(query, params or {})
                    return [dict(record) for record in result]
            except (ServiceUnavailable, TransientError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)  # Exponential backoff
        raise last_error
    
    def execute_cypher_write(
        self, 
        query: str, 
        params: dict[str, Any] | None = None,
    ) -> None:
        """Execute a write Cypher query."""
        with self._driver.session() as session:
            session.run(query, params or {})
    
    def health_check(self) -> bool:
        """Check if Memgraph is accessible."""
        try:
            result = self.execute_cypher("RETURN 1 AS health")
            return len(result) == 1 and result[0].get("health") == 1
        except Exception:
            return False
    
    def close(self) -> None:
        """Close the driver connection."""
        self._driver.close()
    
    def __enter__(self) -> "MemgraphConnection":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


@lru_cache(maxsize=1)
def get_memgraph() -> MemgraphConnection:
    """Get singleton Memgraph connection."""
    config = get_config()
    return MemgraphConnection(config.memgraph_uri)
```

### 4.3 Task 1.3: MCP Client Module

**Prompt**:
```
Create src/triage_agent/mcp/client.py with:

1. Class `MCPClient`:
   - Async HTTP client using httpx
   - Configurable timeout from get_config().mcp_timeout
   - Automatic retry on 429 (rate limit) with exponential backoff

2. Methods:
   - async query_prometheus(query: str, time: int | None = None) -> dict
   - async query_prometheus_range(query: str, start: int, end: int, step: str = "15s") -> dict
   - async query_loki(logql: str, start: int, end: int, limit: int = 1000) -> list[dict]
   - async health_check_prometheus() -> bool
   - async health_check_loki() -> bool

3. Error handling:
   - MCPQueryError for failed queries
   - MCPTimeoutError for timeout
   - Log all queries with timestamps for debugging

Reference existing client.py but rewrite to be async and production-ready.
Use httpx.AsyncClient, not requests.

Include tests in tests/unit/test_mcp_client.py using pytest-httpx for mocking.
```

**Expected Output** (`src/triage_agent/mcp/client.py`):
```python
"""Async MCP client for Prometheus and Loki."""

import logging
from typing import Any

import httpx

from triage_agent.config import get_config

logger = logging.getLogger(__name__)


class MCPQueryError(Exception):
    """Raised when an MCP query fails."""
    pass


class MCPTimeoutError(MCPQueryError):
    """Raised when an MCP query times out."""
    pass


class MCPClient:
    """Async client for Prometheus and Loki via MCP protocol."""
    
    def __init__(
        self, 
        prometheus_url: str | None = None,
        loki_url: str | None = None,
        timeout: float | None = None,
    ):
        config = get_config()
        self.prometheus_url = prometheus_url or config.prometheus_url
        self.loki_url = loki_url or config.loki_url
        self.timeout = timeout or config.mcp_timeout
        self._client: httpx.AsyncClient | None = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def query_prometheus(
        self, 
        query: str, 
        time: int | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Execute instant PromQL query."""
        client = await self._get_client()
        params = {"query": query}
        if time:
            params["time"] = time
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"Prometheus query: {query}")
                response = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("status") != "success":
                    raise MCPQueryError(f"Prometheus error: {data.get('error')}")
                return data.get("data", {})
            except httpx.TimeoutException as e:
                raise MCPTimeoutError(f"Prometheus query timed out: {query}") from e
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise MCPQueryError(f"Prometheus HTTP error: {e}") from e
        raise MCPQueryError(f"Max retries exceeded for query: {query}")
    
    async def query_prometheus_range(
        self,
        query: str,
        start: int,
        end: int,
        step: str = "15s",
    ) -> dict[str, Any]:
        """Execute range PromQL query."""
        client = await self._get_client()
        params = {
            "query": query,
            "start": start,
            "end": end,
            "step": step,
        }
        
        try:
            logger.debug(f"Prometheus range query: {query} [{start}:{end}]")
            response = await client.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "success":
                raise MCPQueryError(f"Prometheus error: {data.get('error')}")
            return data.get("data", {})
        except httpx.TimeoutException as e:
            raise MCPTimeoutError(f"Prometheus range query timed out") from e
        except httpx.HTTPStatusError as e:
            raise MCPQueryError(f"Prometheus HTTP error: {e}") from e
    
    async def query_loki(
        self,
        logql: str,
        start: int,
        end: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Execute LogQL query."""
        client = await self._get_client()
        params = {
            "query": logql,
            "start": start * 1_000_000_000,  # Convert to nanoseconds
            "end": end * 1_000_000_000,
            "limit": limit,
        }
        
        try:
            logger.debug(f"Loki query: {logql}")
            response = await client.get(
                f"{self.loki_url}/loki/api/v1/query_range",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            
            # Parse Loki response format
            logs = []
            for stream in data.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for value in stream.get("values", []):
                    logs.append({
                        "timestamp": int(value[0]) // 1_000_000_000,
                        "message": value[1],
                        "labels": labels,
                    })
            return logs
        except httpx.TimeoutException as e:
            raise MCPTimeoutError(f"Loki query timed out") from e
        except httpx.HTTPStatusError as e:
            raise MCPQueryError(f"Loki HTTP error: {e}") from e
    
    async def health_check_prometheus(self) -> bool:
        """Check Prometheus availability."""
        try:
            result = await self.query_prometheus("up")
            return bool(result.get("result"))
        except Exception:
            return False
    
    async def health_check_loki(self) -> bool:
        """Check Loki availability."""
        client = await self._get_client()
        try:
            response = await client.get(f"{self.loki_url}/ready")
            return response.status_code == 200
        except Exception:
            return False
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
```

---

## 5. Phase 2: Agent Implementation

### 5.1 Task 2.1: InfraAgent (Complete Implementation)

**Prompt**:
```
Complete the implementation of src/triage_agent/agents/infra_agent.py:

CONTEXT:
- InfraAgent is rule-based, NO LLM calls
- Uses MCP to query Prometheus for pod-level metrics
- Computes infrastructure score using 4-factor weighted model
- Always forwards findings to RCAAgent (no early exit)

EXISTING CODE: [paste infra_agent.py content]

REQUIREMENTS:
1. Implement compute_infrastructure_score() using the weight table in comments
2. Implement all extract_* helper functions
3. Make infra_agent() async and wire up MCP client
4. Add @traceable decorator for LangSmith

WEIGHT TABLE:
| Factor | Weight | Scoring Logic |
| Pod Reliability (Restarts) | 0.35 | 0: 0.0, 1-2: 0.4, 3-5: 0.7, >5: 1.0 |
| Critical Errors (OOM) | 0.25 | 0: 0.0, >0: 1.0 |
| Pod Health Status | 0.20 | Running: 0.0, Pending: 0.6, Failed/Unknown: 1.0 |
| Resource Saturation | 0.20 | Mem>90%: 1.0, CPU>1.0core: 0.8, Normal: 0.0 |

Include tests in tests/unit/test_infra_agent.py covering:
- Score computation with various metric combinations
- Edge cases (empty metrics, all zeros)
- MCP query error handling
```

**Expected Implementation Pattern**:
```python
"""InfraAgent: Infrastructure triage via Prometheus pod metrics."""

from datetime import datetime
from typing import Any

from langsmith import traceable

from triage_agent.mcp.client import MCPClient
from triage_agent.state import TriageState

# Constants
WEIGHT_RESTARTS = 0.35
WEIGHT_OOM = 0.25
WEIGHT_POD_STATUS = 0.20
WEIGHT_RESOURCES = 0.20

INFRA_PROMETHEUS_QUERIES = [
    # ... (existing queries)
]


def compute_restart_score(restart_count: int) -> float:
    """Score based on pod restart count."""
    if restart_count == 0:
        return 0.0
    elif restart_count <= 2:
        return 0.4
    elif restart_count <= 5:
        return 0.7
    else:
        return 1.0


def compute_oom_score(oom_count: int) -> float:
    """Score based on OOM kill events."""
    return 1.0 if oom_count > 0 else 0.0


def compute_pod_status_score(status: str) -> float:
    """Score based on pod status."""
    status_scores = {
        "Running": 0.0,
        "Pending": 0.6,
        "Failed": 1.0,
        "Unknown": 1.0,
    }
    return status_scores.get(status, 1.0)


def compute_resource_score(cpu_usage: float, memory_percent: float) -> float:
    """Score based on resource saturation."""
    if memory_percent > 90:
        return 1.0
    elif cpu_usage > 1.0:
        return 0.8
    return 0.0


def compute_infrastructure_score(metrics: dict[str, Any]) -> float:
    """Compute weighted infrastructure score from pod metrics."""
    restart_score = compute_restart_score(
        sum(m.get("value", 0) for m in metrics.get("pod_restarts", []))
    )
    oom_score = compute_oom_score(
        sum(m.get("value", 0) for m in metrics.get("oom_kills", []))
    )
    
    # Find worst pod status
    statuses = metrics.get("pod_status", [])
    pod_status_score = max(
        (compute_pod_status_score(s.get("phase", "Unknown")) for s in statuses),
        default=0.0
    )
    
    # Find worst resource usage
    cpu_max = max((m.get("value", 0) for m in metrics.get("cpu_usage", [])), default=0)
    mem_max = max((m.get("value", 0) for m in metrics.get("memory_percent", [])), default=0)
    resource_score = compute_resource_score(cpu_max, mem_max)
    
    # Weighted sum
    total = (
        WEIGHT_RESTARTS * restart_score +
        WEIGHT_OOM * oom_score +
        WEIGHT_POD_STATUS * pod_status_score +
        WEIGHT_RESOURCES * resource_score
    )
    return min(total, 1.0)


def parse_timestamp(ts: str) -> int:
    """Parse ISO timestamp to Unix epoch."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp())


@traceable(name="InfraAgent")
async def infra_agent(state: TriageState, mcp_client: MCPClient) -> TriageState:
    """InfraAgent entry point. Rule-based, no LLM."""
    alert = state["alert"]
    alert_time = parse_timestamp(alert["startsAt"])
    
    # Query all infrastructure metrics
    metrics: dict[str, list] = {
        "pod_restarts": [],
        "oom_kills": [],
        "cpu_usage": [],
        "memory_percent": [],
        "pod_status": [],
    }
    
    for query in INFRA_PROMETHEUS_QUERIES:
        try:
            result = await mcp_client.query_prometheus(query, time=alert_time)
            # Parse and categorize result based on 'report' label
            for item in result.get("result", []):
                report_type = item.get("metric", {}).get("report")
                if report_type in metrics:
                    metrics[report_type].append({
                        "pod": item.get("metric", {}).get("pod"),
                        "container": item.get("metric", {}).get("container"),
                        "value": float(item.get("value", [0, 0])[1]),
                        "phase": item.get("metric", {}).get("phase"),
                    })
        except Exception as e:
            # Log but continue - partial data is better than none
            import logging
            logging.warning(f"InfraAgent query failed: {e}")
    
    infra_score = compute_infrastructure_score(metrics)
    
    # Update state
    state["infra_checked"] = True
    state["infra_score"] = infra_score
    state["infra_findings"] = {
        "pod_restarts": metrics["pod_restarts"],
        "oom_kills": metrics["oom_kills"],
        "resource_usage": {
            "cpu": metrics["cpu_usage"],
            "memory": metrics["memory_percent"],
        },
        "pod_status": metrics["pod_status"],
    }
    
    return state
```

### 5.2 Task 2.2: NfMetricsAgent (Complete Implementation)

**Prompt**:
```
Complete the implementation of src/triage_agent/agents/metrics_agent.py:

CONTEXT:
- NfMetricsAgent is deterministic, NO LLM calls
- Queries Prometheus for per-NF metrics: error rate, p95 latency, CPU, memory
- NF list comes from state["dag"]["all_nfs"]

EXISTING CODE: [paste metrics_agent.py content]

REQUIREMENTS:
1. Make metrics_agent() async and wire up MCP client
2. Implement organize_metrics_by_nf() to group results by NF name
3. Add @traceable decorator
4. Handle partial failures gracefully (some queries fail, others succeed)

Include tests with mocked MCP responses.
```

### 5.3 Task 2.3: NfLogsAgent (Complete Implementation)

**Prompt**:
```
Complete the implementation of src/triage_agent/agents/logs_agent.py:

CONTEXT:
- NfLogsAgent is deterministic, NO LLM calls
- Queries Loki for ERROR/WARN/FATAL logs
- Annotates logs with matched DAG phase using wildcard_match()
- Pattern matching: '*' matches any characters, case-insensitive

EXISTING CODE: [paste logs_agent.py content]

REQUIREMENTS:
1. Make logs_agent() async and wire up MCP client
2. Implement extract_nf_from_pod_name() using regex (pod names like "amf-deployment-xyz-123")
3. The wildcard_match() function is already implemented - use it for phase annotation
4. Add @traceable decorator

Include tests covering:
- Log parsing and NF extraction
- Wildcard pattern matching
- Phase annotation
```

### 5.4 Task 2.4: UeTracesAgent (Complete Implementation)

**Prompt**:
```
Complete the implementation of src/triage_agent/agents/ue_traces_agent.py:

CONTEXT:
- UeTracesAgent is deterministic, NO LLM calls
- Pipeline: IMSI discovery → Per-IMSI trace construction → Memgraph ingestion → Deviation detection
- Uses Loki for log queries, Memgraph for trace storage and DAG comparison

EXISTING CODE: [paste ue_traces_agent.py content]

REQUIREMENTS:
1. Implement loki_query() using MCP client
2. Implement extract_unique_imsis() - parse IMSI from log messages (format: "imsi-<15 digits>")
3. Implement per_imsi_logql() - build LogQL query filtering by IMSI
4. Implement contract_imsi_trace() - construct trace events from raw logs
5. Implement ingest_traces_to_memgraph() using MemgraphConnection
6. Implement run_deviation_detection() with this Cypher:

```cypher
// Find first deviation point between captured trace and reference DAG
MATCH (ref:ReferenceTrace {name: $dag_name})-[:STEP]->(refStep:RefEvent)
MATCH (trace:CapturedTrace {incident_id: $incident_id, imsi: $imsi})-[:EVENT]->(event:TraceEvent)
WHERE refStep.order = event.order AND NOT event.action CONTAINS refStep.action
RETURN refStep.order AS deviation_point, refStep.action AS expected, event.action AS actual
ORDER BY refStep.order
LIMIT 1
```

Include comprehensive tests.
```

### 5.5 Task 2.5: RCAAgent (Complete Implementation)

**Prompt**:
```
Complete the implementation of src/triage_agent/agents/rca_agent.py:

CONTEXT:
- RCAAgent is the ONLY agent that uses an LLM
- Receives all evidence: infra_findings, metrics, logs, trace_deviations, dag
- Outputs: layer, root_nf, failure_mode, confidence, evidence_chain

EXISTING CODE: [paste rca_agent.py content]

REQUIREMENTS:
1. Implement llm_analyze_evidence() using LangChain with structured output
2. Use Pydantic model for response validation:

```python
class RCAOutput(BaseModel):
    layer: Literal["infrastructure", "application"]
    root_nf: str
    failure_mode: str
    failed_phase: Optional[str]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_chain: List[EvidenceItem]
    alternative_hypotheses: List[Hypothesis]
    reasoning: str
```

3. Add timeout handling with degraded mode fallback
4. Implement generate_final_report() 
5. Implement identify_evidence_gaps() for second attempt
6. Add @traceable decorator

Include tests with mocked LLM responses.
```

---

## 6. Phase 3: LangGraph Orchestration

### 6.1 Task 3.1: LangGraph Workflow Definition

**Prompt**:
```
Create src/triage_agent/graph.py implementing the full LangGraph workflow:

ARCHITECTURE (from triageagent_architecture_design2.md):
```
START → [InfraAgent | DataCollection] (parallel)
        ↓
DataCollection → NfMetricsAgent → NfLogsAgent → UeTracesAgent → EvidenceQuality
        ↓
[InfraAgent done, EvidenceQuality done] → RCAAgent
        ↓
RCAAgent → [confident?] → END
           [not confident, attempt < max] → SecondAttempt → RCAAgent
           [not confident, attempt >= max] → END
```

REQUIREMENTS:
1. Use StateGraph with TriageState
2. Parallel execution: InfraAgent runs parallel to DataCollection group
3. DataCollection group: metrics → logs → traces → evidence_quality (sequential)
4. Conditional routing based on confidence threshold
5. Maximum 2 attempts

EXAMPLE PATTERN:
```python
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

def create_workflow() -> StateGraph:
    workflow = StateGraph(TriageState)
    
    # Add nodes
    workflow.add_node("infra", infra_agent)
    workflow.add_node("metrics", metrics_agent)
    workflow.add_node("logs", logs_agent)
    workflow.add_node("traces", ue_traces_agent)
    workflow.add_node("evidence_quality", compute_evidence_quality)
    workflow.add_node("rca", rca_agent_first_attempt)
    workflow.add_node("finalize", finalize_report)
    
    # Add edges...
    
    return workflow.compile()
```

Include visualization test that draws the graph.
```

**Expected Implementation** (`src/triage_agent/graph.py`):
```python
"""LangGraph workflow definition for TriageAgent."""

from typing import Literal

from langgraph.graph import StateGraph, START, END

from triage_agent.state import TriageState
from triage_agent.agents.infra_agent import infra_agent
from triage_agent.agents.metrics_agent import metrics_agent
from triage_agent.agents.logs_agent import logs_agent
from triage_agent.agents.ue_traces_agent import discover_and_trace_imsis
from triage_agent.agents.evidence_quality import compute_evidence_quality
from triage_agent.agents.rca_agent import rca_agent_first_attempt


def should_retry(state: TriageState) -> Literal["retry", "finalize"]:
    """Determine if RCA should retry with more evidence."""
    if state.get("needs_more_evidence") and state.get("attempt_count", 1) < state.get("max_attempts", 2):
        return "retry"
    return "finalize"


def increment_attempt(state: TriageState) -> TriageState:
    """Increment attempt counter before retry."""
    state["attempt_count"] = state.get("attempt_count", 1) + 1
    return state


def finalize_report(state: TriageState) -> TriageState:
    """Finalize the RCA report."""
    state["final_report"] = {
        "incident_id": state.get("incident_id"),
        "layer": state.get("layer"),
        "root_nf": state.get("root_nf"),
        "failure_mode": state.get("failure_mode"),
        "confidence": state.get("confidence"),
        "evidence_chain": state.get("evidence_chain", []),
        "infra_score": state.get("infra_score"),
        "evidence_quality_score": state.get("evidence_quality_score"),
        "degraded_mode": state.get("degraded_mode", False),
    }
    return state


def create_workflow() -> StateGraph:
    """Create the TriageAgent LangGraph workflow."""
    
    # Create workflow with state schema
    workflow = StateGraph(TriageState)
    
    # Add all nodes
    workflow.add_node("infra_agent", infra_agent)
    workflow.add_node("metrics_agent", metrics_agent)
    workflow.add_node("logs_agent", logs_agent)
    workflow.add_node("traces_agent", discover_and_trace_imsis)
    workflow.add_node("evidence_quality", compute_evidence_quality)
    workflow.add_node("rca_agent", rca_agent_first_attempt)
    workflow.add_node("increment_attempt", increment_attempt)
    workflow.add_node("finalize", finalize_report)
    
    # Parallel start: InfraAgent and DataCollection run simultaneously
    # Using Send for parallel execution
    workflow.add_edge(START, "infra_agent")
    workflow.add_edge(START, "metrics_agent")
    
    # Data collection pipeline (sequential within parallel branch)
    workflow.add_edge("metrics_agent", "logs_agent")
    workflow.add_edge("logs_agent", "traces_agent")
    workflow.add_edge("traces_agent", "evidence_quality")
    
    # Both branches must complete before RCA
    # InfraAgent and EvidenceQuality both flow to RCA
    workflow.add_edge("infra_agent", "rca_agent")
    workflow.add_edge("evidence_quality", "rca_agent")
    
    # Conditional routing after RCA
    workflow.add_conditional_edges(
        "rca_agent",
        should_retry,
        {
            "retry": "increment_attempt",
            "finalize": "finalize",
        }
    )
    
    # Retry loop
    workflow.add_edge("increment_attempt", "rca_agent")
    
    # End state
    workflow.add_edge("finalize", END)
    
    return workflow.compile()


def get_initial_state(alert: dict, incident_id: str) -> TriageState:
    """Create initial state from alert payload."""
    return TriageState(
        alert=alert,
        incident_id=incident_id,
        infra_checked=False,
        infra_score=0.0,
        infra_findings=None,
        procedure_name=None,
        dag_id=None,
        dag=None,
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
        second_attempt_complete=False,
        final_report=None,
    )
```

### 6.2 Task 3.2: FastAPI Webhook

**Prompt**:
```
Create src/triage_agent/api/webhook.py with:

1. FastAPI app with POST /webhook endpoint
2. Pydantic models for Alertmanager webhook payload
3. Async execution of LangGraph workflow
4. Health check endpoint GET /health
5. Proper error handling and logging

Include:
- CORS middleware for development
- Request ID generation for tracing
- LangSmith project configuration

Test with sample Alertmanager payload.
```

---

## 7. Phase 4: Integration & Testing

### 7.1 Task 4.1: Integration Tests

**Prompt**:
```
Create tests/integration/test_full_pipeline.py with:

1. Test fixture that:
   - Starts local Memgraph container
   - Loads reference DAGs
   - Mocks Prometheus/Loki responses

2. Test cases:
   - test_registration_failure_infrastructure_root_cause
   - test_registration_failure_application_root_cause
   - test_pdu_session_failure_with_trace_deviation
   - test_low_confidence_triggers_retry
   - test_degraded_mode_on_llm_timeout

Use pytest-docker for container management.
```

### 7.2 Task 4.2: End-to-End Test

**Prompt**:
```
Create tests/e2e/test_webhook_e2e.py with:

1. Full webhook test against running system
2. Sample Alertmanager payloads for each scenario
3. Validation of final_report structure
4. LangSmith trace verification

Requires: --alert-webhook flag for target URL
```

---

## 8. Subagent Definitions

Create these project-specific subagents in `.claude/agents/`:

### 8.1 Memgraph Expert Subagent

Create `.claude/agents/memgraph-expert.md`:

```markdown
---
name: memgraph-expert
description: Expert in Memgraph/Cypher for DAG queries and deviation detection. Use when working with graph database code, Cypher queries, or trace comparison logic.
tools: Read, Grep, Glob, Bash
model: sonnet
---

You are a Memgraph/Cypher expert specializing in graph database operations for the 5G TriageAgent project.

Key knowledge:
- Memgraph uses Bolt protocol (port 7687), compatible with Neo4j Python driver
- Reference DAGs are stored as :ReferenceTrace and :RefEvent nodes
- Captured traces are :CapturedTrace and :TraceEvent nodes
- Deviation detection uses subgraph pattern matching

When reviewing Cypher code:
1. Check for injection vulnerabilities (always use parameterized queries)
2. Verify index usage for large traversals
3. Ensure proper node/relationship cleanup
4. Validate Bolt connection handling

Common patterns in this project:
```cypher
// Load reference DAG
MATCH (t:ReferenceTrace {name: $dag_name})-[:STEP]->(e:RefEvent)
RETURN e ORDER BY e.order

// Ingest captured trace
CREATE (t:CapturedTrace {incident_id: $id, imsi: $imsi})
WITH t UNWIND $events AS event
CREATE (t)-[:EVENT]->(e:TraceEvent {order: event.order, action: event.action})

// Deviation detection
MATCH (ref:ReferenceTrace {name: $dag})-[:STEP]->(r:RefEvent)
MATCH (trace:CapturedTrace {incident_id: $id})-[:EVENT]->(e:TraceEvent)
WHERE r.order = e.order AND NOT e.action CONTAINS r.action
RETURN r.order AS deviation_point
```

When asked to write or review Cypher:
- Always use parameterized queries
- Check for index recommendations
- Validate cleanup of orphan nodes
- Test with mgconsole before committing
```

### 8.2 PromQL Builder Subagent

Create `.claude/agents/promql-builder.md`:

```markdown
---
name: promql-builder
description: Expert in PromQL for Kubernetes/5G metrics. Use when building or debugging Prometheus queries for pod metrics, NF performance, or infrastructure health.
tools: Read, Grep, Bash
model: sonnet
---

You are a Prometheus/PromQL expert for Kubernetes observability in 5G networks.

Key metrics in this project:
- kube_pod_container_status_restarts_total
- container_cpu_usage_seconds_total
- container_memory_working_set_bytes
- kube_pod_status_phase
- http_requests_total (NF SBI endpoints)
- http_request_duration_seconds

PromQL patterns for 5G NFs:
```promql
# Pod restarts in last hour
sum by (pod) (increase(kube_pod_container_status_restarts_total{namespace="5g-core"}[1h]))

# OOM kills
increase(kube_pod_container_status_restarts_total{namespace="5g-core"}[5m])
* on(pod, container) group_left(reason)
kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}

# NF error rate
rate(http_requests_total{nf="amf", status=~"5.."}[1m])

# P95 latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{nf="amf"}[5m]))
```

When reviewing PromQL:
1. Verify label matchers are specific enough
2. Check rate() vs increase() usage
3. Validate time windows match use case
4. Ensure aggregation doesn't lose important dimensions
```

### 8.3 5G Protocol Reviewer Subagent

Create `.claude/agents/5g-protocol-reviewer.md`:

```markdown
---
name: 5g-protocol-reviewer
description: Expert in 3GPP 5G specifications (TS 23.502, TS 33.501). Use when reviewing DAG definitions, protocol flows, or NF interactions.
tools: Read, Grep
model: sonnet
---

You are a 5G protocol expert reviewing code for 3GPP specification compliance.

Key specifications:
- TS 23.502: Procedures for the 5G System
- TS 33.501: Security architecture and procedures
- TS 29.500-29.518: SBI protocols

Registration procedure (TS 23.502 4.2.2.2.2):
1. UE → AMF: Registration Request
2. AMF selection
3. AMF → AUSF: Authentication (5G AKA)
4. AMF → UDM: Registration, subscription data
5. AMF → PCF: Policy association
6. AMF → UE: Registration Accept

5G AKA (TS 33.501 6.1.3.2):
1. AMF → AUSF: Nausf_UEAuthentication_Authenticate
2. AUSF → UDM: Nudm_UEAuthentication_Get
3. UDM → AUSF: Authentication vectors
4. AUSF → AMF: Auth response
5. AMF → UE: Authentication Request
6. UE → AMF: Authentication Response

NF naming:
- AMF: Access and Mobility Management Function
- SMF: Session Management Function
- UPF: User Plane Function
- NRF: Network Repository Function
- AUSF: Authentication Server Function
- UDM: Unified Data Management
- UDR: Unified Data Repository
- PCF: Policy Control Function
- NSSF: Network Slice Selection Function

When reviewing:
1. Verify NF names match 3GPP terminology
2. Check procedure step ordering against specs
3. Validate message names and parameters
4. Ensure 5G AKA (not EAP-AKA') is used for authentication
```

---

## 9. Common Patterns & Anti-Patterns

### 9.1 Effective Claude Code Prompts

**✅ Good Pattern: Context → Task → Constraints → Verification**

```
CONTEXT:
I'm implementing the NfLogsAgent for the 5G TriageAgent project.
The agent queries Loki via MCP for ERROR/WARN/FATAL logs.
See existing logs_agent.py for the skeleton code.

TASK:
Complete the logs_agent() function to:
1. Build LogQL queries for each NF in dag["all_nfs"]
2. Execute queries via MCP client
3. Annotate logs with matched DAG phases using wildcard_match()

CONSTRAINTS:
- Must be async (use await for MCP calls)
- No LLM calls - this is a deterministic agent
- Use @traceable decorator for LangSmith
- Handle partial failures (some NFs may have no logs)

VERIFICATION:
After implementing, run:
pytest tests/unit/test_logs_agent.py -v
```

**❌ Bad Pattern: Vague Request**

```
Implement the logs agent to query logs and match patterns.
```

### 9.2 Session Management

**✅ Good: One Module Per Session**

```bash
# Session 1: InfraAgent
claude "Implement infra_agent.py..."
# Review, test, commit

# /clear

# Session 2: MetricsAgent  
claude "Implement metrics_agent.py..."
# Review, test, commit
```

**❌ Bad: Everything in One Session**

```bash
claude "Implement all 5 agents, the LangGraph workflow, and the FastAPI endpoint"
# Context overload, degraded quality
```

### 9.3 Error Recovery

**When Claude Makes a Mistake**:

1. **First attempt**: Provide specific correction
   ```
   The wildcard_match function should use re.search, not re.match.
   re.match only matches at the start of the string.
   Fix this specific issue.
   ```

2. **Second attempt**: If still wrong, `/clear` and rewrite prompt
   ```
   /clear
   
   Create wildcard_match(text: str, pattern: str) -> bool that:
   - Converts '*' in pattern to '.*' regex
   - Uses re.search for substring matching
   - Is case-insensitive
   
   Test cases:
   - wildcard_match("ERROR in authentication", "*auth*") → True
   - wildcard_match("WARN memory", "*auth*") → False
   - wildcard_match("Auth failed", "*AUTH*") → True (case-insensitive)
   ```

---

## 10. Troubleshooting Guide

### 10.1 Common Issues

| Issue | Diagnosis | Solution |
|-------|-----------|----------|
| Memgraph connection refused | Port 7687 not exposed | Check k8s service, verify sidecar running |
| MCP timeout | Prometheus/Loki overloaded | Increase MCP_TIMEOUT, check cluster health |
| LLM timeout in RCAAgent | Prompt too long | Reduce evidence in prompt, use degraded mode |
| DAG not found | Init container failed | Check init container logs, reload DAGs manually |
| Trace deviation empty | IMSI not in logs | Extend time window, check log retention |

### 10.2 Debug Commands

```bash
# Check Memgraph status
mgconsole -host localhost -port 7687 <<< "SHOW STORAGE INFO;"

# List loaded DAGs
mgconsole <<< "MATCH (t:ReferenceTrace) RETURN t.name;"

# Test Prometheus connectivity
curl -s 'http://prometheus:9090/api/v1/query?query=up' | jq '.status'

# Test Loki connectivity
curl -s 'http://loki:3100/ready'

# View LangSmith traces
# Open: https://smith.langchain.com/project/5g-triage-agent-v3

# Run specific test with verbose output
pytest tests/unit/test_infra_agent.py::test_compute_score -v --tb=long
```

### 10.3 Performance Tuning

| Bottleneck | Metric | Target | Tuning |
|------------|--------|--------|--------|
| MCP queries | P95 latency | <500ms | Parallel queries, caching |
| LLM analysis | Token usage | <4000 | Reduce prompt size, structured output |
| Memgraph | Query time | <100ms | Add indexes on order, incident_id |
| Total pipeline | E2E latency | <5.5s | Parallel agent execution |

---

## Appendix A: Memgraph Sidecar

For complete Memgraph documentation including:
- Local development setup (Docker)
- Kubernetes deployment (sidecar pattern)
- DAG schema and loading
- Python integration
- Testing strategy (unit + integration)
- Operations and debugging

**See: [docs/memgraph-sidecar-guide.md](docs/memgraph-sidecar-guide.md)**

### Quick Start (Local Development)

```bash
# Start Memgraph
docker run -d --name memgraph-dev -p 7687:7687 memgraph/memgraph

# Load DAGs
./scripts/load_dags.sh

# Verify
mgconsole --host localhost --port 7687 <<< "MATCH (t:ReferenceTrace) RETURN t.name;"

# Run integration tests
pytest tests/integration/test_memgraph_integration.py -v
```

### Quick Start (Docker Compose)

```bash
# Start all services (Memgraph, Prometheus, Loki, TriageAgent)
docker-compose up -d

# View logs
docker-compose logs -f triage-agent

# Stop
docker-compose down
```

---

## Appendix B: Complete File Scaffolds

### A.1 `src/triage_agent/__init__.py`

```python
"""5G TriageAgent - Multi-Agent LangGraph RCA System."""

__version__ = "3.2.0"

from triage_agent.config import get_config
from triage_agent.graph import create_workflow, get_initial_state
from triage_agent.state import TriageState

__all__ = [
    "get_config",
    "create_workflow", 
    "get_initial_state",
    "TriageState",
]
```

### A.2 `tests/conftest.py`

```python
"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from triage_agent.mcp.client import MCPClient
from triage_agent.memgraph.connection import MemgraphConnection


@pytest.fixture
def mock_mcp_client():
    """Mock MCP client for unit tests."""
    client = AsyncMock(spec=MCPClient)
    client.query_prometheus.return_value = {"result": []}
    client.query_loki.return_value = []
    return client


@pytest.fixture
def mock_memgraph():
    """Mock Memgraph connection for unit tests."""
    conn = MagicMock(spec=MemgraphConnection)
    conn.execute_cypher.return_value = []
    conn.health_check.return_value = True
    return conn


@pytest.fixture
def sample_alert():
    """Sample Alertmanager webhook payload."""
    return {
        "status": "firing",
        "labels": {
            "alertname": "RegistrationFailures",
            "severity": "critical",
            "namespace": "5g-core",
            "nf": "amf",
        },
        "annotations": {
            "summary": "Registration failures detected",
        },
        "startsAt": "2026-02-15T10:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_dag():
    """Sample DAG structure."""
    return {
        "name": "Registration_General",
        "all_nfs": ["AMF", "AUSF", "UDM", "NRF", "PCF"],
        "phases": [
            {
                "phase_id": "auth",
                "actors": ["AMF", "AUSF"],
                "success_log": "Authentication successful",
                "failure_patterns": ["*auth*fail*", "*timeout*AUSF*"],
            },
        ],
    }
```

---

*End of Claude Code Development Guide*
