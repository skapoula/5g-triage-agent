# 5G TriageAgent v3.2

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A multi-agent LangGraph orchestration system for real-time root cause analysis of 5G core network failures.

## Overview

When Prometheus Alertmanager fires an alert (e.g., `registration_failures > 0`), TriageAgent coordinates specialized agents through a directed graph workflow to localize failures across infrastructure, network function, and 3GPP procedure layers.

```
InfraAgent (parallel) → NfMetricsAgent + NfLogsAgent + UeTracesAgent (parallel) → EvidenceQuality → RCAAgent
```

### Key Features

- **Multi-Agent Architecture**: 5 specialized agents, only 1 uses LLM (RCAAgent)
- **MCP Protocol**: Standardized interface to Prometheus and Loki
- **3GPP DAG Comparison**: Reference procedure DAGs in Memgraph for trace deviation detection
- **LangSmith Observability**: Full tracing and feedback loops for confidence calibration

## Architecture

| Agent | Purpose | LLM? |
|-------|---------|------|
| **InfraAgent** | Infrastructure triage (pod metrics) | No (rule-based) |
| **NfMetricsAgent** | Per-NF Prometheus metrics | No (MCP query) |
| **NfLogsAgent** | Per-NF Loki logs | No (MCP query) |
| **UeTracesAgent** | IMSI trace construction + deviation detection | No (MCP + Memgraph) |
| **RCAAgent** | Root cause analysis | Yes |

## Quick Start

### Prerequisites

- Python 3.11+
- Memgraph (Bolt protocol, port 7687)
- Prometheus and Loki (for data sources)
- LLM API key (OpenAI, Anthropic, etc.)

### Installation

```bash
# Clone the repository
git clone https://github.com/youruser/5g-triage-agent.git
cd 5g-triage-agent

# Install dependencies
pip install -e ".[dev]"

# Set environment variables
export LLM_API_KEY=your-api-key
export MEMGRAPH_HOST=localhost
export PROMETHEUS_URL=http://prometheus:9090
export LOKI_URL=http://loki:3100
```

### Load Reference DAGs

```bash
# Start Memgraph
docker run -p 7687:7687 memgraph/memgraph

# Load 3GPP procedure DAGs
mgconsole < dags/registration_general.cypher
mgconsole < dags/authentication_5g_aka.cypher
mgconsole < dags/pdu_session_establishment.cypher
```

### Run the Service

```bash
# Start the webhook server
uvicorn triage_agent.api.webhook:app --host 0.0.0.0 --port 8000

# Test health endpoint
curl http://localhost:8000/health
```

### Kubernetes Deployment

```bash
# Apply manifests
kubectl apply -f k8s/deployment-with-init.yaml
kubectl apply -f k8s/alertmanager-webhook.yaml
```

## Development

### Test-First Workflow

This project follows **test-first development**. Always write tests before implementing:

```bash
# 1. Write tests first
claude "Write pytest tests for XyzAgent... Don't implement yet."

# 2. Review and approve tests

# 3. Implement to pass tests
claude "Implement XyzAgent to pass these tests: [paste tests]"

# 4. Verify
pytest tests/unit/test_xyz_agent.py -v
mypy src/triage_agent/agents/xyz_agent.py --strict
ruff check src/triage_agent/agents/xyz_agent.py

# 5. Commit
```

### Running Tests

```bash
# Unit tests
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ --cov=triage_agent --cov-report=html

# Integration tests (requires Memgraph)
pytest tests/integration/ --memgraph-url bolt://localhost:7687
```

## Project Structure

```
5g-triage-agent/
├── CLAUDE.md                    # Claude Code conventions
├── pyproject.toml               # Dependencies
├── src/triage_agent/
│   ├── config.py                # Configuration
│   ├── state.py                 # TriageState TypedDict
│   ├── graph.py                 # LangGraph workflow
│   ├── agents/                  # Agent implementations
│   ├── mcp/                     # MCP client
│   ├── memgraph/                # Memgraph connection
│   └── api/                     # FastAPI webhook
├── dags/                        # 3GPP procedure DAGs (Cypher)
├── k8s/                         # Kubernetes manifests
├── tests/                       # Test suite
└── .claude/agents/              # Claude Code subagents
```

## Documentation

- [Architecture Design (PRD)](docs/triageagent_architecture_design2.md)
- [Claude Code Development Guide](claude-code-development-guide.md)
- [API Documentation](http://localhost:8000/docs) (when running)

## License

MIT License - see [LICENSE](LICENSE) for details.
