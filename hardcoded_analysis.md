# Hard-Coded Values Analysis — 5G TriageAgent

**Scope:** All Python files under `src/triage_agent/`
**Date:** 2026-02-19
**Purpose:** Catalogue every hard-coded value that affects agent behaviour, with location, purpose, and recommended configuration grouping.

---

## Table of Contents

1. [Summary Table](#summary-table)
2. [Detailed Analysis by File](#detailed-analysis-by-file)
   - [config.py](#configpy)
   - [graph.py](#graphpy)
   - [agents/infra_agent.py](#agentsinfra_agentpy)
   - [agents/metrics_agent.py](#agentsmetrics_agentpy)
   - [agents/logs_agent.py](#agentslogs_agentpy)
   - [agents/ue_traces_agent.py](#agentsue_traces_agentpy)
   - [agents/rca_agent.py](#agentsrca_agentpy)
   - [agents/evidence_quality.py](#agentsevidence_qualitypy)
   - [mcp/client.py](#mcpclientpy)
   - [memgraph/connection.py](#memgraphconnectionpy)
   - [api/webhook.py](#apiwebhookpy)
   - [__main__.py](#__main__py)
3. [Recommended Configuration Groupings](#recommended-configuration-groupings)

---

## Summary Table

| Value | Current Value | File | Line | Recommended Group |
|-------|--------------|------|------|-------------------|
| `memgraph_host` | `"localhost"` | config.py | 14 | `infra_config` |
| `memgraph_port` | `7687` | config.py | 15 | `infra_config` |
| `prometheus_url` | `"http://kube-prom-...prometheus.monitoring:9090"` | config.py | 18 | `api_config` |
| `loki_url` | `"http://loki.monitoring:3100"` | config.py | 19 | `api_config` |
| `mcp_timeout` | `3.0` (seconds) | config.py | 20 | `api_config` |
| `llm_model` | `"qwen3-4b-instruct-2507.Q4_K_M.gguf"` | config.py | 24 | `model_config` |
| `llm_timeout` | `300` (seconds) | config.py | 25 | `model_config` |
| `llm_provider` | `"local"` | config.py | 26 | `model_config` |
| `llm_base_url` | `"http://qwen3-4b.ml-serving.svc.cluster.local/v1"` | config.py | 31 | `model_config` |
| `core_namespace` | `"5g-core"` | config.py | 37 | `infra_config` |
| `langsmith_project` | `"5g-triage-agent"` | config.py | 41 | `observability_config` |
| `max_attempts` | `2` | graph.py | 149 | `agent_config` |
| `should_retry` fallback | `2` | graph.py | 25 | `agent_config` |
| Pod restarts PromQL window | `[1h]` | infra_agent.py | 37 | `query_config` |
| OOM kills PromQL window | `[5m]` | infra_agent.py | 43 | `query_config` |
| CPU rate PromQL window | `[2m]` | infra_agent.py | 50 | `query_config` |
| Restart weight | `0.35` | infra_agent.py | 96 | `scoring_config` |
| OOM weight | `0.25` | infra_agent.py | 100 | `scoring_config` |
| Pod status weight | `0.20` | infra_agent.py | 111 | `scoring_config` |
| Resource saturation weight | `0.20` | infra_agent.py | 128 | `scoring_config` |
| Restart threshold (critical) | `5` | infra_agent.py | 88 | `scoring_config` |
| Restart threshold (moderate) | `3` | infra_agent.py | 90 | `scoring_config` |
| Restart threshold (low) | `1` | infra_agent.py | 92 | `scoring_config` |
| Restart factor (3-5 restarts) | `0.7` | infra_agent.py | 91 | `scoring_config` |
| Restart factor (1-2 restarts) | `0.4` | infra_agent.py | 93 | `scoring_config` |
| Pending pod status factor | `0.6` | infra_agent.py | 110 | `scoring_config` |
| Memory saturation threshold | `90` (%) | infra_agent.py | 122 | `scoring_config` |
| CPU saturation threshold | `1.0` (cores) | infra_agent.py | 124 | `scoring_config` |
| CPU saturation factor | `0.8` | infra_agent.py | 125 | `scoring_config` |
| InfraAgent alert time window | `-300s / +60s` | infra_agent.py | 268 | `agent_config` |
| Excessive restart threshold | `5` | infra_agent.py | 212 | `scoring_config` |
| Error rate PromQL window | `[1m]` | metrics_agent.py | 92 | `query_config` |
| Latency quantile | `0.95` | metrics_agent.py | 93 | `query_config` |
| CPU rate PromQL window (NF) | `[5m]` | metrics_agent.py | 94 | `query_config` |
| LogsAgent lookback window | `-300s` | logs_agent.py | 267 | `agent_config` |
| LogsAgent lookahead window | `+60s` | logs_agent.py | 268 | `agent_config` |
| Loki query limit (direct HTTP) | `1000` | logs_agent.py | 235 | `query_config` |
| IMSI digit length | `15` | ue_traces_agent.py | 35 | `agent_config` |
| IMSI discovery namespace | `"5g-core"` (hardcoded in LogQL) | ue_traces_agent.py | 296 | `infra_config` |
| IMSI discovery window | `±30s` | ue_traces_agent.py | 298 | `agent_config` |
| Per-IMSI trace lookback | `-120s` | ue_traces_agent.py | 307 | `agent_config` |
| Per-IMSI trace lookahead | `+60s` | ue_traces_agent.py | 307 | `agent_config` |
| Loki query limit (direct HTTP) | `1000` | ue_traces_agent.py | 172 | `query_config` |
| LLM temperature | `0.1` | rca_agent.py | 180, 193, 208 | `model_config` |
| Infra root cause threshold | `0.80` | rca_agent.py | 83 (prompt) | `scoring_config` |
| Infra-triggered threshold | `0.60` | rca_agent.py | 84 (prompt) | `scoring_config` |
| App-only threshold | `0.30` | rca_agent.py | 85 (prompt) | `scoring_config` |
| Low evidence quality threshold | `0.50` | rca_agent.py | 331 | `scoring_config` |
| Infra detail required threshold | `0.60` | rca_agent.py | 334 | `scoring_config` |
| Low confidence threshold | `0.70` | rca_agent.py | 338 | `scoring_config` |
| Degraded infra confidence | `0.50` | rca_agent.py | 364 | `scoring_config` |
| Degraded specific event confidence | `0.60` | rca_agent.py | 371, 374 | `scoring_config` |
| Degraded app confidence | `0.40` | rca_agent.py | 381 | `scoring_config` |
| Degraded pattern-match confidence | `0.50` | rca_agent.py | 393, 398 | `scoring_config` |
| Degraded mode infra threshold | `0.80` | rca_agent.py | 358 | `scoring_config` |
| Default min confidence (retry gate) | `0.70` | rca_agent.py | 461 | `agent_config` |
| Lowered min confidence | `0.65` | rca_agent.py | 462 | `agent_config` |
| High evidence quality threshold | `0.80` | rca_agent.py | 462 | `scoring_config` |
| Evidence quality — all sources | `0.95` | evidence_quality.py | 17 | `scoring_config` |
| Evidence quality — traces + 1 | `0.85` | evidence_quality.py | 19 | `scoring_config` |
| Evidence quality — metrics + logs | `0.80` | evidence_quality.py | 20 | `scoring_config` |
| Evidence quality — traces only | `0.50` | evidence_quality.py | 22 | `scoring_config` |
| Evidence quality — metrics only | `0.40` | evidence_quality.py | 23 | `scoring_config` |
| Evidence quality — logs only | `0.35` | evidence_quality.py | 24 | `scoring_config` |
| Evidence quality — no evidence | `0.10` | evidence_quality.py | 26 | `scoring_config` |
| Prometheus max retries | `3` | mcp/client.py | 51 | `api_config` |
| Prometheus range step | `"15s"` | mcp/client.py | 92 | `query_config` |
| Loki query limit (MCPClient) | `1000` | mcp/client.py | 124 | `query_config` |
| Rate-limit backoff | `2**attempt` seconds | mcp/client.py | 77 | `api_config` |
| Memgraph pool size | `10` | memgraph/connection.py | 16 | `infra_config` |
| Memgraph max retries | `3` | memgraph/connection.py | 27 | `infra_config` |
| Memgraph backoff | `2**attempt` seconds | memgraph/connection.py | 38 | `infra_config` |
| FastAPI app version | `"3.2.0"` | api/webhook.py | 21 | *(build metadata)* |
| CORS allow origins | `["*"]` | api/webhook.py | 27 | `api_config` |
| Alertmanager webhook version | `"4"` | api/webhook.py | 92 | `api_config` |
| Server host | `"0.0.0.0"` | __main__.py | 39 | `api_config` |
| Server port | `8000` | __main__.py | 43 | `api_config` |

---

## Detailed Analysis by File

---

### `config.py`

This is the canonical configuration module — values here are explicitly designed to be overridden via environment variables. However, their defaults still constitute hard-coded values.

#### Infrastructure Connectivity

| Value | Line | Default | Purpose / Impact |
|-------|------|---------|-----------------|
| `memgraph_host` | 14 | `"localhost"` | Bolt connection host. Defaulting to localhost is only suitable for local dev — in-cluster deployments need the Memgraph sidecar service DNS name. |
| `memgraph_port` | 15 | `7687` | Bolt protocol port. Matches Memgraph default, but a misconfiguration here silently breaks all graph queries (no fallback). |

#### API / MCP Endpoints

| Value | Line | Default | Purpose / Impact |
|-------|------|---------|-----------------|
| `prometheus_url` | 18 | `"http://kube-prom-kube-prometheus-prometheus.monitoring:9090"` | Full in-cluster DNS path for Prometheus. Tied to a specific Helm release name (`kube-prom`). Any change to the Helm chart name or monitoring namespace breaks all metric collection without a code change. |
| `loki_url` | 19 | `"http://loki.monitoring:3100"` | Loki in-cluster DNS. Same concern as Prometheus: namespace `monitoring` is baked into the default. |
| `mcp_timeout` | 20 | `3.0` | Seconds for HTTP requests to Prometheus/Loki. Very aggressive — on a busy cluster this triggers fallback paths or silent empty results. If this value is too low, agents degrade silently. |

#### LLM / Model

| Value | Line | Default | Purpose / Impact |
|-------|------|---------|-----------------|
| `llm_model` | 24 | `"qwen3-4b-instruct-2507.Q4_K_M.gguf"` | Model filename for local vLLM/Ollama. This is provider-specific; switching providers without updating this name causes inference errors. |
| `llm_timeout` | 25 | `300` (seconds) | Maximum time to wait for an LLM response. Exceeding this threshold triggers `degraded_mode_analysis()` — a rule-based fallback with significantly lower confidence scores (0.40–0.60 vs. normal 0.70+). This is the single most consequential timeout in the system. |
| `llm_provider` | 26 | `"local"` | Selects the LangChain backend. Controls which branch of `create_llm()` runs. An incorrect default here causes the entire RCA pipeline to fail at startup. |
| `llm_base_url` | 31 | `"http://qwen3-4b.ml-serving.svc.cluster.local/v1"` | In-cluster KServe service URL for the local provider. The service name `qwen3-4b` must match the deployed KServe InferenceService name exactly. |

#### Kubernetes

| Value | Line | Default | Purpose / Impact |
|-------|------|---------|-----------------|
| `core_namespace` | 37 | `"5g-core"` | The K8s namespace label used in all Prometheus and Loki queries. Changing the deployment namespace without updating this silently returns empty data for all agents. |

#### Observability

| Value | Line | Default | Purpose / Impact |
|-------|------|---------|-----------------|
| `langsmith_project` | 41 | `"5g-triage-agent"` | LangSmith project name for traces/feedback. Incorrect value causes traces to be sent to the wrong project, making debugging much harder. |

---

### `graph.py`

Controls the retry / loop structure of the LangGraph workflow.

#### `get_initial_state()` — line 114–153

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `attempt_count=1` | 148 | literal `1` | Initial attempt counter. Always 1-based; changing this would skip the first attempt numbering. |
| `max_attempts=2` | 149 | literal `2` | **Hard limit on RCA retries.** Only 1 retry is ever permitted. If the first attempt lacks confidence and triggers a retry, it runs exactly once more — then finalizes regardless of confidence. Increasing this would allow more thorough evidence gathering at the cost of latency. |
| `infra_score=0.0` | 128 | literal `0.0` | Initial infrastructure score. Zero means "no infra problem" — any agent that reads this before InfraAgent completes will see a clean slate. |
| `confidence=0.0` | 144 | literal `0.0` | Initial RCA confidence. Forces the confidence gate in `rca_agent_first_attempt` to always request a real LLM analysis first. |

#### `should_retry()` — line 21–29

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| Fallback `max_attempts` | 25 | `state.get("max_attempts", 2)` | Defensive default — if state is missing the key, it assumes 2. This is a duplicate of the `get_initial_state` default; a mismatch between the two would create inconsistent retry behaviour. |

---

### `agents/infra_agent.py`

This is the most numerically dense file. All values are purely hard-coded (no env-var backing). Changes require a code deployment.

#### 5G NF Definitions

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `_KNOWN_NFS` | 16–18 | `frozenset({"amf", "smf", "upf", "nrf", "ausf", "udm", "udr", "pcf", "nssf"})` | Controls NF extraction from alert labels / pod name fallback. Any new NF not in this set is silently ignored during alert parsing. |
| `_NF_CONTAINER_RE` | 22 | `"^(nrf|pcf|amf|smf|ausf|udm|udr|upf|nssf|mongodb).*"` | Prometheus container-level regex filter. `mongodb` is included here as a dependency of Open5GS but not in `_KNOWN_NFS`, which is an inconsistency. A new NF or dependency must be added to both patterns manually. |
| `_NF_POD_RE` | 23 | same as above | Prometheus pod-level regex filter. Same concerns as `_NF_CONTAINER_RE`. |

#### PromQL Time Windows (`build_infra_queries`)

| Value | Lines | Hard-coded as | Purpose / Impact |
|-------|-------|--------------|-----------------|
| Restart window | 37 | `[1h]` | Sum of restarts over the past hour. Too long a window includes historical restarts unrelated to the current incident, inflating the restart factor and potentially misattributing incidents to infrastructure. |
| OOM kills window | 43 | `[5m]` | Recent OOM kills. Short window is appropriate for acute events; extending it risks false positives. |
| CPU rate window | 50 | `[2m]` | CPU usage rate-of-change. A very short window; can be noisy on bursty workloads. |

#### Scoring Weights (`compute_infrastructure_score`) — lines 79–130

The four weights must sum to 1.0. Currently: `0.35 + 0.25 + 0.20 + 0.20 = 1.00`.

| Factor | Weight (line) | Scoring breakpoints | Impact |
|--------|--------------|---------------------|--------|
| Pod Restarts | `0.35` (line 96) | 0→0.0, 1–2→0.4, 3–5→0.7, >5→1.0 | Heaviest single factor. A pod restarting 6+ times automatically contributes `0.35` to the score. The thresholds of 3 and 5 are arbitrary; in stable clusters 3 restarts in 1h may be normal. |
| OOM Kills | `0.25` (line 100) | 0→0.0, any→1.0 | Binary: a single OOM kill immediately adds `0.25`. This is intentionally aggressive since OOM is always a problem signal. |
| Pod Status | `0.20` (line 111) | Running→0.0, Pending→0.6, Failed/Unknown→1.0 | A Pending pod (e.g. during rolling update) adds `0.12` to the score; a failed pod adds `0.20`. The `0.6` factor for Pending is conservative — some operators may prefer it weighted heavier or lighter. |
| Resource Saturation | `0.20` (line 128) | mem>90%→1.0, CPU>1.0core→0.8, else→0.0 | Memory threshold of `90%` and CPU threshold of `1.0` core are absolute and not relative to pod resource limits (memory) or request (CPU), which can produce false positives/negatives. |

#### Restart Count Thresholds

| Value | Line | Purpose |
|-------|------|---------|
| `> 5` → factor `1.0` | 88 | "Excessive" restarts: maximum penalty. Also used in `extract_critical_events` at line 212. |
| `>= 3` → factor `0.7` | 90 | "High" restarts: significant but not critical. |
| `>= 1` → factor `0.4` | 92 | "Some" restarts: mild concern. |

#### Alert Time Window (`infra_agent`) — line 268

```python
time_window = (alert_time - 300, alert_time + 60)  # -5min to +60s
```

| Offset | Value | Impact |
|--------|-------|--------|
| Lookback | `-300s` (5 minutes) | How far before alert start to query. Too short misses slow-developing failures; too long includes unrelated events. |
| Lookahead | `+60s` | How far after alert start to include. Captures cascading effects shortly after the incident begins. |

---

### `agents/metrics_agent.py`

#### PromQL Query Parameters (`build_nf_queries`) — lines 91–95

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| Error rate window | 92 | `[1m]` | Rolling 1-minute error rate per NF. Very short — spikes shorter than 1 min may average out; the window should match the incident timescale. |
| Latency quantile | 93 | `0.95` | p95 HTTP latency. This is a common SLO boundary, but some operators target p99 or p99.9 for reliability-critical functions like AUSF. Hardcoding p95 means tail-latency issues at p99+ are invisible to the agent. |
| CPU rate window | 94 | `[5m]` | CPU usage rate over the past 5 minutes. Longer than the error rate window — appropriate for smoothing CPU noise. |

---

### `agents/logs_agent.py`

#### Loki Query Time Window (`logs_agent`) — lines 267–268

```python
start = int(alert_time - 300)   # -5 minutes
end   = int(alert_time + 60)    # +60 seconds
```

These match InfraAgent's window exactly. All agents sharing the same window is intentional but the values are duplicated across files, creating a maintenance hazard: changing one does not change the other.

#### Loki Log Limit — line 235

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `limit` | 235 | `1000` | Maximum log lines returned per LogQL query. For high-traffic NFs (e.g. AMF, SMF), 1000 lines may be insufficient during an incident storm, truncating evidence before the most relevant logs. Also appears in MCPClient at line 124 and `ue_traces_agent.py` at line 172. |

---

### `agents/ue_traces_agent.py`

#### IMSI Pattern — line 35

```python
_IMSI_PATTERN = re.compile(r"(?i)imsi-(\d{15})")
```

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| IMSI digit count | 35 | `15` | ITU-T E.212 defines IMSIs as up to 15 digits. This is correct per spec but cannot be adjusted without code change if a private network uses shorter identifiers. |

#### Hardcoded Namespace in LogQL — line 296

```python
discovery_logql = '{k8s_namespace_name="5g-core"} |~ "(?i)imsi-"'
```

**This is the most critical duplication in the codebase.** The namespace `"5g-core"` is baked into the LogQL string literal here instead of using `get_config().core_namespace`. This means:
- Changing `CORE_NAMESPACE` via env var fixes all other agents but **not** IMSI discovery.
- If the 5G core is deployed to a different namespace, UeTracesAgent silently finds zero IMSIs.

Compare: `logs_agent.py` correctly uses `get_config().core_namespace` in its queries.

#### Time Windows — lines 298, 307

| Purpose | Offset | Lines | Impact |
|---------|--------|-------|--------|
| IMSI discovery window | `±30s` around alert | 298 | Narrow: only IMSIs active within ±30s of alert time are discovered. For slow-developing failures the incident may have started earlier. |
| Per-IMSI trace lookback | `-120s` (2 minutes) | 307 | Wider than the discovery window to capture the full procedure leading up to the failure. |
| Per-IMSI trace lookahead | `+60s` | 307 | Matches the other agents' lookahead. |

---

### `agents/rca_agent.py`

#### LLM Temperature — lines 180, 193, 208

```python
temperature=0.1   # openai, anthropic, and local providers
```

`temperature=0.1` is applied identically to all three LLM providers. A near-zero temperature maximises determinism, which is appropriate for structured JSON output. However, it means the LLM will almost never explore alternative hypotheses beyond the most probable. For genuinely ambiguous failures, a slightly higher temperature (0.2–0.3) may produce better-calibrated confidence scores.

#### RCA Prompt — Infra Score Thresholds (lines 83–85)

These thresholds appear in the **LLM system prompt** and guide the model's layer determination:

```
If infra_score >= 0.80: Likely infrastructure root cause
If infra_score >= 0.60: Possible infrastructure-triggered application failure
If infra_score < 0.30: Likely pure application failure
```

These exact values are critical because they define the model's decision logic. They must be consistent with the infra scoring weights in `infra_agent.py`. Example: the scoring model can produce a maximum `infra_score` of `1.0`, but scores of `0.60–0.80` can occur from a combination of moderate restarts + Pending pods without any OOM — the prompt correctly labels these as "possible infrastructure-triggered".

#### `identify_evidence_gaps()` — lines 308–342

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| Low evidence quality | 331 | `0.50` | Below this score, the gaps list includes "Overall evidence quality too low". Since `evidence_quality_score` cannot go below `0.10` (no-evidence case), this catches the lower half of all evidence states. |
| Infra detail threshold | 334 | `0.60` | If infra_score > 0.60 but no infra_findings, a gap is flagged. Consistent with the infra-triggered threshold in the prompt. |
| Low confidence | 338 | `0.70` | Catches cases where no specific gap is found but confidence is low anyway. This matches the default `min_confidence` used as the retry gate (line 461). |

#### `degraded_mode_analysis()` — lines 345–411

When the LLM times out, this rule-based fallback runs. All confidence values here are intentionally lower than normal LLM output to signal lower reliability:

| Condition | Confidence | Line |
|-----------|-----------|------|
| Infrastructure root cause (generic) | `0.50` | 364 |
| OOMKilled event detected | `0.60` | 371 |
| CrashLoopBackOff detected | `0.60` | 374 |
| Application layer (unknown) | `0.40` | 381 |
| Timeout keyword in logs | `0.50` | 393 |
| Auth failure keyword in logs | `0.50` | 398 |
| Infra threshold for degraded mode | `0.80` | 358 |

Note: the degraded mode infra threshold (`0.80`) is stricter than the prompt's infrastructure threshold (`0.80` is also the prompt boundary) but differs from the "possible infra-triggered" threshold of `0.60`. This means a score of `0.65` routes to application layer in degraded mode but would be labelled "possible infrastructure-triggered" by the LLM.

#### Confidence Retry Gate — lines 461–470

```python
min_confidence = 0.70
if state.get("evidence_quality_score", 0.0) >= 0.80:
    min_confidence = 0.65
```

| Value | Line | Purpose / Impact |
|-------|------|-----------------|
| `0.70` | 461 | Default minimum confidence below which the agent requests a retry (second attempt). Acts as the primary quality gate for the entire pipeline. |
| `0.65` | 462 | Relaxed gate when evidence quality is high (≥0.80). Rationale: with rich evidence, slightly lower model confidence is acceptable. The difference (5%) is small and may not meaningfully change outcomes. |
| `0.80` | 462 | Evidence quality threshold to activate the relaxed confidence gate. Corresponds to the "metrics + logs" evidence quality score in `evidence_quality.py`. |

---

### `agents/evidence_quality.py`

#### Quality Score Lookup Table — lines 16–29

This is a pure hard-coded scoring table with no configuration backing whatsoever.

| Condition | Score | Line | Rationale / Impact |
|-----------|-------|------|-------------------|
| Metrics + Logs + Traces | `0.95` | 17 | Near-perfect evidence. With all three sources, RCA has full context. Score is not `1.0` to leave room for perfect correlation. |
| Traces + one other | `0.85` | 19 | Traces are weighted heavily because they provide the most direct procedural evidence of 5G signalling failures. |
| Metrics + Logs (no traces) | `0.80` | 20 | Sufficient for typical failures. This is the threshold that unlocks the lowered confidence gate in `rca_agent.py` (line 462). |
| Traces only | `0.50` | 22 | Traces without metrics or logs provide temporal evidence but no system state context. |
| Metrics only | `0.40` | 23 | Resource metrics without logs miss application-layer signals. |
| Logs only | `0.35` | 24 | Logs without metrics cannot confirm resource contention. Slightly lower than metrics-only because logs are noisier. |
| No evidence | `0.10` | 26 | Sentinel value. Not `0.0` to allow some minimal scoring headroom. Triggers "Overall evidence quality too low" gap in `rca_agent.py`. |

**Key interaction:** The `0.80` score for "Metrics + Logs" directly determines whether the confidence retry gate is lowered from `0.70` → `0.65` in `rca_agent.py`. This cross-file coupling is not documented.

---

### `mcp/client.py`

#### Prometheus Query Retries

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `max_retries` | 51 | `3` | Number of retry attempts for Prometheus queries on HTTP 429 (rate limit). The retry logic uses exponential backoff (`2**attempt` seconds: 1s, 2s, 4s). With 3 retries + 3.0s timeout per attempt, worst-case latency from this alone is `~11s`, exceeding `mcp_timeout`. |
| Rate-limit backoff | 77 | `2**attempt` | Base-2 exponential backoff in seconds. Only triggered on HTTP 429. On a heavily loaded cluster, 3 attempts with exponential backoff may still fail during sustained rate-limit periods. |

#### Prometheus Range Query Step — line 92

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `step` | 92 | `"15s"` | Default resolution for range queries. Currently `query_prometheus_range` is defined but not called by any agent. When it is used, `15s` steps over a 5-minute window return 20 data points — adequate granularity for most purposes. |

#### Loki Query Limit — line 124

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `limit` | 124 | `1000` | Maximum log entries returned by `query_loki()`. Identical to the direct-HTTP limit but defined separately. If this limit is hit, later log entries are silently dropped. There is no pagination or warning. |

---

### `memgraph/connection.py`

#### Connection Pool — line 16

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `max_connection_pool_size` | 16 | `10` | Maximum concurrent Bolt connections to Memgraph. Since Memgraph runs as a sidecar with a single replica, 10 connections is generous. Raising this unnecessarily consumes Memgraph's session capacity. |

#### Cypher Query Retries — lines 27–38

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `max_retries` | 27 | `3` | Retry attempts for `ServiceUnavailable` or `TransientError`. Mirrors MCP client retry count. |
| Backoff | 38 | `2**attempt` seconds | Exponential backoff: 1s → 2s → 4s. Total worst-case wait before final failure: `7s`. This adds to overall pipeline latency on an unstable Memgraph connection. |

---

### `api/webhook.py`

#### FastAPI App Metadata — line 18–22

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| App version | 21 | `"3.2.0"` | Also returned in the root `/` endpoint (line 184). Not driven from `pyproject.toml` or any build system variable. Must be updated manually on release; easy to forget. |

#### CORS Configuration — lines 25–31

```python
allow_origins=["*"],
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"],
```

This is an open CORS policy suitable for development only. In production, `allow_origins` should be restricted to the Alertmanager service IP/hostname. The comment at line 24 says "for development" but this is the only CORS configuration in the codebase — there is no production override path.

#### Alertmanager Webhook Version — line 92

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| `version` | 92 | `"4"` | Alertmanager webhook API version. This matches Alertmanager v0.22+. Changing the Alertmanager major version may change the payload schema; this default would then silently accept incompatible payloads. |

---

### `__main__.py`

#### CLI Defaults — lines 39–43

| Value | Line | Hard-coded as | Purpose / Impact |
|-------|------|--------------|-----------------|
| Server host | 39 | `"0.0.0.0"` | Binds to all interfaces. Appropriate for container deployments but not local development where `127.0.0.1` would be safer. |
| Server port | 43 | `8000` | Default uvicorn port. Must match `k8s/` service and Alertmanager receiver config. A mismatch here only affects CLI invocations, not Docker/Kubernetes deployments (which use their own port mappings). |

---

## Recommended Configuration Groupings

The following groups represent logical configuration boundaries. Values within a group change together (same operator, same deployment concern) and should be co-located in a configuration object or config file section.

---

### `model_config` — LLM / Model Parameters

Controls the behaviour and performance of the RCA LLM call.

```
llm_provider          = "local"          # config.py:26
llm_model             = "qwen3-4b-..."   # config.py:24
llm_base_url          = "http://qwen3-4b.ml-serving..."  # config.py:31
llm_api_key           = ""              # config.py:23
llm_timeout           = 300            # config.py:25  — triggers degraded mode
llm_temperature       = 0.1            # rca_agent.py:180,193,208 — NOT in config.py yet
```

**Notable gap:** `temperature` is hard-coded in `rca_agent.py` directly inside `create_llm()` and is not exposed via `TriageAgentConfig` at all. It should be added as `llm_temperature`.

---

### `agent_config` — Pipeline Flow / Retry Logic

Controls how the LangGraph workflow routes and retries.

```
max_attempts             = 2     # graph.py:149
min_confidence_default   = 0.70  # rca_agent.py:461 — retry gate
min_confidence_relaxed   = 0.65  # rca_agent.py:462 — retry gate with good evidence
high_evidence_threshold  = 0.80  # rca_agent.py:462 — gates min_confidence_relaxed
alert_lookback_seconds   = 300   # infra_agent.py:268, logs_agent.py:267, ue_traces_agent.py implicitly
alert_lookahead_seconds  = 60    # infra_agent.py:268, logs_agent.py:268
imsi_discovery_window_seconds = 30   # ue_traces_agent.py:298
imsi_trace_lookback_seconds   = 120  # ue_traces_agent.py:307
```

**Notable gap:** The alert time window (`-300s / +60s`) is duplicated across `infra_agent.py`, `logs_agent.py`, and `ue_traces_agent.py` independently. It should be a single shared constant.

---

### `scoring_config` — Thresholds and Weights

All numeric thresholds used in rule-based scoring. These are the most operationally sensitive values — tuning them changes the classification of every alert.

```
# Infra scoring weights (must sum to 1.0)
infra_weight_restarts    = 0.35   # infra_agent.py:96
infra_weight_oom         = 0.25   # infra_agent.py:100
infra_weight_pod_status  = 0.20   # infra_agent.py:111
infra_weight_resources   = 0.20   # infra_agent.py:128

# Infra scoring restart breakpoints
restart_threshold_critical  = 5   # infra_agent.py:88
restart_threshold_high      = 3   # infra_agent.py:90
restart_threshold_low       = 1   # infra_agent.py:92
restart_factor_high         = 0.7 # infra_agent.py:91
restart_factor_low          = 0.4 # infra_agent.py:93
restart_factor_critical_event = 5 # infra_agent.py:212 (also used in extract_critical_events)

# Infra scoring resource breakpoints
memory_saturation_pct       = 90  # infra_agent.py:122
cpu_saturation_cores        = 1.0 # infra_agent.py:124
cpu_saturation_factor       = 0.8 # infra_agent.py:125
pod_pending_factor          = 0.6 # infra_agent.py:110

# RCA layer determination thresholds (used in LLM prompt AND degraded mode)
infra_root_cause_threshold  = 0.80  # rca_agent.py:83 (prompt), 358 (degraded)
infra_triggered_threshold   = 0.60  # rca_agent.py:84 (prompt), 334 (identify_evidence_gaps)
app_only_threshold          = 0.30  # rca_agent.py:85 (prompt only)

# Degraded mode confidence values
degraded_conf_infra_generic     = 0.50  # rca_agent.py:364
degraded_conf_infra_specific    = 0.60  # rca_agent.py:371,374
degraded_conf_app_unknown       = 0.40  # rca_agent.py:381
degraded_conf_app_pattern_match = 0.50  # rca_agent.py:393,398

# Evidence gap thresholds
evidence_gap_quality_threshold     = 0.50  # rca_agent.py:331
evidence_gap_confidence_threshold  = 0.70  # rca_agent.py:338

# Evidence quality scores
eq_score_all_sources      = 0.95  # evidence_quality.py:17
eq_score_traces_plus_one  = 0.85  # evidence_quality.py:19
eq_score_metrics_logs     = 0.80  # evidence_quality.py:20
eq_score_traces_only      = 0.50  # evidence_quality.py:22
eq_score_metrics_only     = 0.40  # evidence_quality.py:23
eq_score_logs_only        = 0.35  # evidence_quality.py:24
eq_score_no_evidence      = 0.10  # evidence_quality.py:26
```

---

### `query_config` — PromQL / LogQL Parameters

Controls the shape of queries sent to Prometheus and Loki.

```
promql_restart_window      = "1h"   # infra_agent.py:37
promql_oom_window          = "5m"   # infra_agent.py:43
promql_cpu_rate_window_infra = "2m" # infra_agent.py:50
promql_error_rate_window   = "1m"   # metrics_agent.py:92
promql_cpu_rate_window_nf  = "5m"   # metrics_agent.py:94
promql_latency_quantile    = 0.95   # metrics_agent.py:93
promql_range_step          = "15s"  # mcp/client.py:92
loki_query_limit           = 1000   # mcp/client.py:124, logs_agent.py:235, ue_traces_agent.py:172
```

**Notable gap:** `loki_query_limit` appears independently in three places (`MCPClient.query_loki`, `_fetch_loki_logs_direct` in `logs_agent.py`, and `_fetch_loki_logs_direct` in `ue_traces_agent.py`) with the same value `1000` but no shared constant.

---

### `api_config` — Service Connectivity and Timeouts

Controls how the system connects to external services and handles HTTP.

```
prometheus_url          = "http://kube-prom-...prometheus.monitoring:9090"  # config.py:18
loki_url                = "http://loki.monitoring:3100"                       # config.py:19
mcp_timeout             = 3.0  # config.py:20 — also used as httpx client timeout
prometheus_max_retries  = 3    # mcp/client.py:51
cors_allow_origins      = ["*"]  # webhook.py:27 — open in production
server_host             = "0.0.0.0"  # __main__.py:39
server_port             = 8000       # __main__.py:43
```

---

### `infra_config` — Database and Cluster Infrastructure

```
memgraph_host                = "localhost"  # config.py:14
memgraph_port                = 7687         # config.py:15
memgraph_max_connection_pool = 10           # memgraph/connection.py:16
memgraph_max_retries         = 3            # memgraph/connection.py:27
core_namespace               = "5g-core"    # config.py:37
                                            # also hardcoded in ue_traces_agent.py:296 — BUG
known_nfs                    = ["amf", "smf", "upf", "nrf", "ausf", "udm", "udr", "pcf", "nssf"]
                                            # infra_agent.py:16-18
```

---

### `observability_config` — Tracing and Monitoring

```
langsmith_project  = "5g-triage-agent"  # config.py:41
langsmith_api_key  = ""                 # config.py:42
app_version        = "3.2.0"            # webhook.py:21 — should be sourced from pyproject.toml
```

---

## Key Issues Requiring Immediate Attention

1. **`ue_traces_agent.py:296` — Namespace hardcoded in LogQL string**
   The IMSI discovery query uses `k8s_namespace_name="5g-core"` literally instead of `get_config().core_namespace`. This is the only agent that ignores the `CORE_NAMESPACE` env var, making IMSI tracing silently broken in any non-default namespace deployment.

2. **Alert time window duplicated across three agents**
   `alert_lookback=300s` and `alert_lookahead=60s` are copied identically into `infra_agent.py:268`, `logs_agent.py:267-268`, and implicitly in `ue_traces_agent.py`. A shared module-level constant or config value would prevent drift.

3. **`loki_query_limit=1000` duplicated three times**
   Defined independently in `mcp/client.py:124`, `logs_agent.py:235`, and `ue_traces_agent.py:172`. If a high-volume incident exceeds 1000 logs, all three limits truncate silently with no warning or pagination.

4. **`llm_temperature` not in `TriageAgentConfig`**
   Temperature is hard-coded to `0.1` inside `create_llm()` for all three providers but is not configurable via environment variable. This prevents tuning without a code change.

5. **Scoring weights and thresholds in `infra_agent.py` and `evidence_quality.py` are entirely code-only**
   These are the most operationally impactful values (they determine whether an incident is classified as infrastructure vs. application), yet they have no configuration pathway. Changing any weight requires a deployment.

6. **Inconsistency: `_KNOWN_NFS` vs. `_NF_CONTAINER_RE`**
   `mongodb` appears in the Prometheus regex patterns (lines 22–23) but not in `_KNOWN_NFS` (lines 16–18). This means MongoDB pods are monitored for restarts/OOM/resource pressure but are never included as a candidate NF in alert label extraction.
