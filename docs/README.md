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
