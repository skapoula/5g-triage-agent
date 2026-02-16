# Claude Code CLI Workflow for TriageAgent

## Your Environment

```
k3s cluster
└── namespace: claudex
    └── pod: sentig-0 (2/2 containers - devcontainer + sidecar)
        └── Claude Code CLI installed
        └── Project: /workspace/5g-triage-agent
```

---

## Recommended Workflow: Session-Per-Module with Test-First

The key insight: **Claude Code works best with focused, single-module sessions**. Don't try to build everything in one session—context window exhaustion leads to degraded output quality.

### Phase Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 1: Bootstrap (1 session)                                     │
│  - Clone/extract project scaffold                                   │
│  - Verify dependencies install                                      │
│  - Load CLAUDE.md into context                                      │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 2: Core Infrastructure (3-4 sessions)                        │
│  - Session 1: config.py + tests                                     │
│  - Session 2: mcp/client.py + tests                                 │
│  - Session 3: memgraph/connection.py + tests                        │
│  - Session 4: api/webhook.py + tests                                │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 3: Agents (5 sessions, one per agent)                        │
│  - Session per agent: tests first → implement → verify              │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 4: Orchestration (1-2 sessions)                              │
│  - LangGraph workflow                                               │
│  - Integration tests                                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Bootstrap

### Step 1.1: Setup Project in Devcontainer

```bash
# Exec into your devcontainer
kubectl exec -it sentig-0 -n claudex -c code-server -- bash

# Create workspace
mkdir -p /workspace/5g-triage-agent
cd /workspace/5g-triage-agent

# Option A: Clone from GitHub (if you've pushed the scaffold)
git clone https://github.com/youruser/5g-triage-agent.git .

# Option B: Extract the zip (if uploaded to cluster)
unzip /tmp/5g-triage-agent.zip -d /workspace/
mv /workspace/5g-triage-agent/* .

# Install dependencies
pip install -e ".[dev]"

# Verify Claude Code CLI is available
claude --version
```

### Step 1.2: Start Memgraph Sidecar (if not already running)

```bash
# Check if Memgraph is running in your pod
curl -s localhost:7687 || echo "Memgraph not on localhost"

# If Memgraph is a separate service:
kubectl get svc -n claudex | grep memgraph

# If you need to start Memgraph locally in devcontainer:
docker run -d --name memgraph -p 7687:7687 memgraph/memgraph:2.14

# Load DAGs
./scripts/load_dags.sh
```

### Step 1.3: First Claude Code Session - Verify Setup

```bash
cd /workspace/5g-triage-agent

# Start Claude Code in interactive mode
claude

# In Claude Code:
> Read CLAUDE.md and verify the project structure is correct. 
> List any missing files from the expected structure.
> Run: pip install -e ".[dev]" and report any issues.
```

**Exit after verification**: `Ctrl+C` or `/exit`

---

## Phase 2: Core Infrastructure

### Session Pattern (Repeat for Each Module)

```bash
# Start fresh session for each module
cd /workspace/5g-triage-agent
claude
```

### Session 2.1: Configuration Module

```
> Read CLAUDE.md for project conventions.

> TASK: Implement config module using test-first approach.

> Step 1: Write pytest tests for src/triage_agent/config.py at tests/unit/test_config.py
> 
> Test cases needed:
> - Default values are correct
> - Environment variable override works  
> - Invalid port raises ValueError
> - Invalid URL raises ValueError
> - memgraph_uri property computed correctly
> - get_config() returns singleton
>
> DO NOT implement config.py yet. Only write the tests.
```

**Review the tests**, then:

```
> Step 2: Implement src/triage_agent/config.py to pass these tests.
> Use pydantic-settings for environment loading.
> Include field validators for port and URL.

> Step 3: Verify
> Run: pytest tests/unit/test_config.py -v
> Run: mypy src/triage_agent/config.py --strict
> Run: ruff check src/triage_agent/config.py
```

**If all pass**: `/exit` and commit:

```bash
git add src/triage_agent/config.py tests/unit/test_config.py
git commit -m "feat(config): implement configuration with tests"
```

### Session 2.2: MCP Client

```bash
claude
```

```
> Read CLAUDE.md. Focus on MCP client requirements.

> TASK: Implement MCP client using test-first approach.

> Step 1: Write pytest tests for src/triage_agent/mcp/client.py at tests/unit/test_mcp_client.py
>
> Test cases:
> - query_prometheus success returns data
> - query_prometheus error raises MCPQueryError
> - query_prometheus timeout raises MCPTimeoutError  
> - query_loki returns parsed log entries
> - query_loki extracts log level correctly
> - health_check_prometheus returns True/False
> - health_check_loki returns True/False
> - Rate limit retry (429 response)
>
> Use pytest-httpx for mocking. DO NOT implement yet.
```

**Review tests**, then:

```
> Step 2: Implement src/triage_agent/mcp/client.py to pass tests.
> Use httpx (async), not requests.
> Include retry logic for 429.
> Extract log level from message text.

> Step 3: Verify
> Run: pytest tests/unit/test_mcp_client.py -v
> Run: mypy src/triage_agent/mcp/client.py --strict
```

**Exit, commit, next session.**

### Session 2.3: Memgraph Connection

```bash
claude
```

```
> Read CLAUDE.md and docs/memgraph-sidecar-guide.md.

> TASK: Implement Memgraph connection using test-first approach.

> Step 1: Write tests at tests/unit/test_memgraph_connection.py
>
> Test cases (with mocked neo4j driver):
> - health_check returns True when connected
> - health_check returns False on error
> - execute_cypher returns list of dicts
> - execute_cypher retries on TransientError
> - load_reference_dag returns correct structure
> - load_reference_dag returns None for missing DAG
> - ingest_captured_trace creates nodes
> - detect_deviation returns deviation dict
> - cleanup_incident_traces removes data
>
> Mock the neo4j driver. DO NOT implement yet.
```

**Review, implement, verify, commit.**

### Session 2.4: FastAPI Webhook

```bash
claude
```

```
> Read CLAUDE.md. Focus on API requirements.

> TASK: Implement webhook API using test-first approach.

> Step 1: Write tests at tests/unit/test_webhook.py
>
> Test cases:
> - GET /health returns status
> - POST /webhook accepts valid AlertmanagerPayload
> - POST /webhook rejects empty alerts (400)
> - POST /webhook skips resolved alerts
> - GET / returns API info
>
> Use TestClient from fastapi.testclient. DO NOT implement yet.
```

**Review, implement, verify, commit.**

---

## Phase 3: Agents (One Session Per Agent)

### Agent Implementation Pattern

For each agent, follow this exact sequence:

```bash
# Fresh session
claude
```

```
> Read CLAUDE.md and src/triage_agent/state.py for TriageState structure.
> Read the existing agent skeleton at src/triage_agent/agents/{agent_name}.py

> TASK: Complete {AgentName} implementation using test-first approach.

> Step 1: Write tests at tests/unit/test_{agent_name}.py
> [specific test cases for this agent]
> DO NOT implement yet.

> Step 2: Review tests with me before implementing.

> Step 3: Complete implementation in src/triage_agent/agents/{agent_name}.py
> Use @traceable decorator for LangSmith observability.
> {agent-specific requirements}

> Step 4: Verify
> Run: pytest tests/unit/test_{agent_name}.py -v
> Run: mypy src/triage_agent/agents/{agent_name}.py --strict
```

### Session 3.1: InfraAgent

```
> Step 1: Write tests for InfraAgent scoring logic.
>
> Test cases:
> - compute_infrastructure_score with healthy metrics → ~0.0
> - compute_infrastructure_score with restarts → weighted score
> - compute_infrastructure_score with OOM → critical weight
> - compute_infrastructure_score with failed pod → weight applied
> - compute_infrastructure_score with resource saturation
> - Score capped at 1.0
> - Empty metrics handled gracefully
> - infra_agent updates state correctly (mock MCP)
```

### Session 3.2: NfMetricsAgent

```
> Step 1: Write tests for NfMetricsAgent.
>
> Test cases:
> - Queries Prometheus for each NF in dag["all_nfs"]
> - Extracts error_rate, p95_latency, cpu, memory
> - Handles partial metric failures gracefully
> - Updates state["metrics"] correctly
> - Handles empty NF list
```

### Session 3.3: NfLogsAgent

```
> Step 1: Write tests for NfLogsAgent.
>
> Test cases:
> - Queries Loki for ERROR/WARN/FATAL logs
> - Annotates logs with matched DAG phase
> - extract_nf_from_pod_name parses correctly
> - wildcard_match handles case-insensitive matching
> - Updates state["logs"] correctly
> - Handles Loki timeout gracefully
```

### Session 3.4: UeTracesAgent

```
> Read docs/memgraph-sidecar-guide.md for trace ingestion patterns.

> Step 1: Write tests for UeTracesAgent.
>
> Test cases:
> - extract_unique_imsis finds IMSI format "imsi-<15 digits>"
> - Ingests traces into Memgraph
> - Detects deviation using Cypher query
> - Handles no IMSIs found
> - Handles Memgraph connection failure
> - Updates state with discovered_imsis, traces_ready, trace_deviations
```

### Session 3.5: RCAAgent (The Only LLM Agent)

```
> Read CLAUDE.md - this is the ONLY agent that uses LLM.

> Step 1: Write tests for RCAAgent.
>
> Test cases:
> - Produces structured RCAOutput (Pydantic model)
> - Confidence threshold logic (0.70 default, 0.65 if evidence_quality ≥ 0.80)
> - Sets needs_more_evidence if confidence below threshold
> - Handles LLM timeout with degraded mode fallback
> - Evidence chain has mandatory citations
> - Updates state with layer, root_nf, failure_mode, confidence
```

---

## Phase 4: Orchestration

### Session 4.1: LangGraph Workflow

```bash
claude
```

```
> Read CLAUDE.md and src/triage_agent/graph.py scaffold.
> Read docs/workflow_diagram.mermaid for expected flow.

> TASK: Complete LangGraph workflow.

> Step 1: Write tests at tests/unit/test_graph.py
>
> Test cases:
> - Workflow compiles without error
> - should_retry returns "retry" when needs_more_evidence=True and attempt < max
> - should_retry returns "finalize" otherwise
> - finalize_report creates final_report dict
> - Parallel edges for infra_agent and metrics_agent
> - Conditional edge from rca_agent

> Step 2: Complete src/triage_agent/graph.py implementation.
> Import all agents.
> Define StateGraph with TriageState.
> Add conditional edges for retry logic.

> Step 3: Verify
> Run: pytest tests/unit/test_graph.py -v
> Run: python -c "from triage_agent.graph import create_workflow; print(create_workflow().get_graph().draw_ascii())"
```

### Session 4.2: Integration Tests

```bash
claude
```

```
> Read tests/integration/test_memgraph_integration.py for patterns.

> TASK: Add integration tests for full pipeline.

> Step 1: Create tests/integration/test_full_pipeline.py
>
> Test scenarios:
> - registration_failure_infrastructure_root_cause
> - registration_failure_application_root_cause  
> - pdu_session_failure_with_trace_deviation
> - low_confidence_triggers_retry
>
> Use fixtures for Memgraph, mock Prometheus/Loki.

> Step 2: Run with real Memgraph
> Run: pytest tests/integration/ -v --memgraph-url bolt://localhost:7687
```

---

## Headless Mode for CI/Automation

For scripted builds or CI pipelines, use headless mode:

```bash
# Single command execution
claude -p "Read CLAUDE.md, then run pytest tests/unit/ -v and report results"

# With specific permission mode
claude --permission-mode plan -p "Analyze the RCAAgent and suggest improvements to confidence calibration"

# Chained commands in script
#!/bin/bash
set -e

cd /workspace/5g-triage-agent

# Run tests
claude -p "Run: pytest tests/unit/ -v --tb=short"

# Type check
claude -p "Run: mypy src/triage_agent/ --strict"

# Lint
claude -p "Run: ruff check src/triage_agent/"
```

---

## Session Management Best Practices

### When to Start a New Session

| Trigger | Action |
|---------|--------|
| Completed a module (tests pass) | `/exit`, commit, new session |
| Claude made same mistake twice | `/clear` or new session |
| Switching to different module | New session |
| Context feels "stale" (repetitive errors) | New session |
| After ~30-40 tool calls | Consider new session |

### Commands Inside Claude Code

```
/clear          # Clear context, stay in session
/compact        # Summarize and reduce context
/exit           # Exit session
Ctrl+C          # Cancel current operation
Ctrl+G          # Open plan in editor (Plan Mode)
```

### Subagent Delegation

For specialized tasks, delegate to subagents:

```
> @memgraph-expert Review the Cypher query in detect_deviation for injection vulnerabilities

> @promql-builder Help me write a PromQL query for NF error rate with proper label matchers

> @5g-protocol-reviewer Verify the Registration_General DAG matches TS 23.502 4.2.2.2.2
```

---

## Quick Reference: Command Sequences

### Daily Development Session

```bash
# 1. Exec into devcontainer
kubectl exec -it sentig-0 -n claudex -c code-server -- bash

# 2. Navigate to project
cd /workspace/5g-triage-agent

# 3. Pull latest
git pull

# 4. Start Claude Code
claude

# 5. Work on ONE module
> Read CLAUDE.md. Today I'm working on {module}.
> [test-first workflow]

# 6. Exit and commit
/exit
git add -A && git commit -m "feat(module): description"
git push
```

### Verify Everything Works

```bash
# In devcontainer
cd /workspace/5g-triage-agent

# Unit tests
pytest tests/unit/ -v

# Type checking
mypy src/triage_agent/ --strict

# Linting
ruff check src/triage_agent/

# Integration tests (requires Memgraph)
pytest tests/integration/ -v --memgraph-url bolt://localhost:7687

# Start the service
uvicorn triage_agent.api.webhook:app --host 0.0.0.0 --port 8000

# Test webhook
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"status":"firing","alerts":[{"status":"firing","labels":{"alertname":"test"}}]}'
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Claude repeats same error | `/clear` and rephrase with more context |
| Tests fail after implementation | Ask Claude to "read the test file and the error, then fix" |
| Memgraph connection refused | Check `docker ps` or k8s service, verify port 7687 |
| Import errors | Run `pip install -e ".[dev]"` again |
| Context too long | Start new session, reference specific files |
| Claude forgets conventions | Start with "Read CLAUDE.md first" |
