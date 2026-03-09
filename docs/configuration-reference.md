# Configuration Reference

All configuration is loaded from environment variables (case-insensitive) or a `.env` file in the
working directory.  `get_config()` returns a singleton `TriageAgentConfig` instance; call it once
at module load time inside your agent.

```python
from triage_agent.config import get_config
cfg = get_config()
```

List-typed fields accept a JSON array string:
```bash
KNOWN_NFS='["amf","smf","custom-nf"]'
CORS_ALLOW_ORIGINS='["http://alertmanager:9093"]'
```

---

## Infrastructure — Database and Cluster

| Variable | Type | Default | Description |
|---|---|---|---|
| `MEMGRAPH_HOST` | `str` | `"localhost"` | Bolt host for the Memgraph sidecar. |
| `MEMGRAPH_PORT` | `int` | `7687` | Bolt port. Must be positive (validated). |
| `MEMGRAPH_POOL_SIZE` | `int` | `10` | Max concurrent Bolt connections. |
| `MEMGRAPH_MAX_RETRIES` | `int` | `3` | Cypher retry attempts on `ServiceUnavailable`/`TransientError`. Uses exponential backoff (`2^attempt` seconds). |
| `KNOWN_NFS` | `list[str]` | `["amf","smf","upf","nrf","ausf","udm","udr","pcf","nssf"]` | NF names used for alert label extraction and pod-name fallback. An NF not in this list is silently ignored during alert parsing. |
| `CORE_NAMESPACE` | `str` | `"5g-core"` | Kubernetes namespace label used in Prometheus/Loki queries. |

---

## Service Connectivity

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROMETHEUS_URL` | `str` | `"http://kube-prom-kube-prometheus-prometheus.monitoring:9090"` | Prometheus base URL. Must start with `http://` or `https://`. |
| `LOKI_URL` | `str` | `"http://loki.monitoring:3100"` | Loki base URL. Must start with `http://` or `https://`. |
| `MCP_TIMEOUT` | `float` | `3.0` | Seconds for HTTP requests to Prometheus/Loki. Aggressive by design; triggers graceful fallback on timeout. |
| `PROMETHEUS_MAX_RETRIES` | `int` | `3` | Retry attempts on HTTP 429 responses from Prometheus. Exponential backoff: `2^attempt` seconds (1 s, 2 s, 4 s). |
| `CORS_ALLOW_ORIGINS` | `list[str]` | `["*"]` | CORS origins for the FastAPI webhook. Restrict to Alertmanager IP in production. |
| `INCIDENT_TTL_SECONDS` | `int` | `3600` | TTL (seconds) for completed/failed incident entries in the in-memory store. Entries are evicted on each new webhook POST. |
| `SERVER_HOST` | `str` | `"0.0.0.0"` | Uvicorn listen host. |
| `SERVER_PORT` | `int` | `8000` | Uvicorn listen port. |

---

## LLM / Model

| Variable | Type | Default | Description |
|---|---|---|---|
| `LLM_API_KEY` | `str` | `""` | API key for `openai` or `anthropic` providers. Required in production. |
| `LLM_MODEL` | `str` | `"qwen3-4b-instruct-2507.Q4_K_M.gguf"` | Model filename (local vLLM/Ollama) or model name (cloud providers). |
| `LLM_TIMEOUT` | `int` | `300` | Seconds to wait for an LLM response before degraded-mode fallback. |
| `LLM_PROVIDER` | `Literal["openai","anthropic","local"]` | `"local"` | Selects the LLM backend: `"openai"` → `ChatOpenAI`, `"anthropic"` → `ChatAnthropic`, `"local"` → `ChatOpenAI` with `llm_base_url`. |
| `LLM_BASE_URL` | `str` | `"http://qwen3-4b.ml-serving.svc.cluster.local/v1"` | OpenAI-compatible base URL for the `local` provider (in-cluster vLLM/Ollama). |
| `LLM_TEMPERATURE` | `float` | `0.1` | Sampling temperature. Near-zero maximises determinism for structured JSON output. |

---

## Pipeline Flow / Retry Logic

| Variable | Type | Default | Description |
|---|---|---|---|
| `MAX_ATTEMPTS` | `int` | `2` | Hard limit on RCA retries. First attempt + (`max_attempts` − 1) retries. |
| `MIN_CONFIDENCE_DEFAULT` | `float` | `0.70` | RCA confidence gate. RCA requests retry when `confidence < min_confidence_default`. |
| `MIN_CONFIDENCE_RELAXED` | `float` | `0.65` | Relaxed confidence gate activated when `evidence_quality_score >= high_evidence_threshold`. |
| `HIGH_EVIDENCE_THRESHOLD` | `float` | `0.80` | Evidence quality score at which `min_confidence_relaxed` activates. Must equal `eq_score_metrics_logs`. |
| `ALERT_LOOKBACK_SECONDS` | `int` | `300` | Query window: seconds *before* alert start time. Used by InfraAgent, NfLogsAgent, and UeTracesAgent. |
| `ALERT_LOOKAHEAD_SECONDS` | `int` | `60` | Query window: seconds *after* alert start time. |
| `IMSI_DISCOVERY_WINDOW_SECONDS` | `int` | `30` | Narrow window around alert time for IMSI discovery queries. |
| `IMSI_TRACE_LOOKBACK_SECONDS` | `int` | `120` | Wider lookback per IMSI to capture the full signalling procedure. |
| `IMSI_DIGIT_LENGTH` | `int` | `15` | Expected IMSI length (ITU-T E.212 defines max 15 digits). Adjust only for private networks. |

---

## PromQL / LogQL Parameters

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROMQL_RESTART_WINDOW` | `str` | `"1h"` | Rolling window for pod restart count queries (`kube_pod_container_status_restarts_total`). |
| `PROMQL_OOM_WINDOW` | `str` | `"5m"` | Rolling window for OOM kill queries (`kube_pod_container_status_last_terminated_reason`). |
| `PROMQL_CPU_RATE_WINDOW_INFRA` | `str` | `"2m"` | `rate()` window for infra-level CPU usage (per-pod `container_cpu_usage_seconds_total`). |
| `PROMQL_ERROR_RATE_WINDOW` | `str` | `"1m"` | `rate()` window for per-NF HTTP error rate queries. |
| `PROMQL_LATENCY_QUANTILE` | `float` | `0.95` | Histogram quantile for per-NF latency queries (0.95 = p95). |
| `PROMQL_CPU_RATE_WINDOW_NF` | `str` | `"5m"` | `rate()` window for per-NF CPU usage queries. |
| `PROMQL_RANGE_STEP` | `str` | `"15s"` | Default resolution step for Prometheus range queries. |
| `LOKI_QUERY_LIMIT` | `int` | `1000` | Maximum log lines returned per LogQL query. Truncation is silent; raise this if high-volume incidents are missing logs. |

---

## Scoring Thresholds

### Infra scoring weights (must sum to 1.0)

| Variable | Type | Default | Dimension |
|---|---|---|---|
| `INFRA_WEIGHT_RESTARTS` | `float` | `0.35` | Pod Reliability (restart count) |
| `INFRA_WEIGHT_OOM` | `float` | `0.25` | Critical Errors (OOM kills) |
| `INFRA_WEIGHT_POD_STATUS` | `float` | `0.20` | Pod Health Status |
| `INFRA_WEIGHT_RESOURCES` | `float` | `0.20` | Resource Saturation (CPU/memory) |

### Restart breakpoints

InfraAgent maps raw restart counts to a score factor using these thresholds:

| Variable | Type | Default | Description |
|---|---|---|---|
| `RESTART_THRESHOLD_CRITICAL` | `int` | `5` | Restart count strictly above this → factor 1.0 (maximum). |
| `RESTART_THRESHOLD_HIGH` | `int` | `3` | Restart count ≥ this → `restart_factor_high`. |
| `RESTART_FACTOR_HIGH` | `float` | `0.7` | Factor when restarts ≥ `restart_threshold_high` and ≤ `restart_threshold_critical`. |
| `RESTART_FACTOR_LOW` | `float` | `0.4` | Factor when restarts ≥ 1 but < `restart_threshold_high`. |

### Resource saturation thresholds

| Variable | Type | Default | Description |
|---|---|---|---|
| `MEMORY_SATURATION_PCT` | `float` | `90.0` | Memory usage % above which `resource_factor = 1.0`. |
| `CPU_SATURATION_CORES` | `float` | `1.0` | CPU (cores) above which `resource_factor = cpu_saturation_factor`. |
| `CPU_SATURATION_FACTOR` | `float` | `0.8` | Resource factor when CPU exceeds `cpu_saturation_cores`. |
| `POD_PENDING_FACTOR` | `float` | `0.6` | Status factor for Pending pods. Failed/Unknown → 1.0. |

### RCA layer determination thresholds

These are inserted into the LLM system prompt to guide root cause classification:

| Variable | Type | Default | Interpretation |
|---|---|---|---|
| `INFRA_ROOT_CAUSE_THRESHOLD` | `float` | `0.80` | `infra_score ≥ this` → infrastructure root cause. |
| `INFRA_TRIGGERED_THRESHOLD` | `float` | `0.60` | `infra_score ≥ this` → possible infra-triggered application failure. |
| `APP_ONLY_THRESHOLD` | `float` | `0.30` | `infra_score < this` → likely pure application failure. |

---

## Evidence Quality and Compression

### Evidence quality scores

`EvidenceQualityAgent` assigns one of these fixed scores based on which data sources are populated:

| Variable | Type | Default | Condition |
|---|---|---|---|
| `EQ_SCORE_ALL_SOURCES` | `float` | `0.95` | metrics + logs + traces all present |
| `EQ_SCORE_TRACES_PLUS_ONE` | `float` | `0.85` | traces + one other source |
| `EQ_SCORE_METRICS_LOGS` | `float` | `0.80` | metrics + logs (no traces). **Must equal `high_evidence_threshold`.** |
| `EQ_SCORE_TRACES_ONLY` | `float` | `0.50` | traces only |
| `EQ_SCORE_METRICS_ONLY` | `float` | `0.40` | metrics only |
| `EQ_SCORE_LOGS_ONLY` | `float` | `0.35` | logs only |
| `EQ_SCORE_NO_EVIDENCE` | `float` | `0.10` | no sources populated (sentinel; not 0.0) |

### Evidence gap thresholds

| Variable | Type | Default | Description |
|---|---|---|---|
| `EVIDENCE_GAP_QUALITY_THRESHOLD` | `float` | `0.50` | Evidence quality below this → "Overall evidence quality too low" gap added. |
| `EVIDENCE_GAP_CONFIDENCE_THRESHOLD` | `float` | `0.70` | When no specific gaps are found but RCA confidence is below this → generic gap flagged. |

### Evidence compression token budgets

`compress_evidence()` in `rca_agent.py` truncates each section to its budget before the LLM prompt.
Total target is ~3 500 tokens, leaving room for the prompt template (~400 tokens) and the LLM response.

| Variable | Type | Default | Section |
|---|---|---|---|
| `RCA_TOKEN_BUDGET_INFRA` | `int` | `400` | Infrastructure evidence section |
| `RCA_TOKEN_BUDGET_DAG` | `int` | `800` | DAG deviation evidence section |
| `RCA_TOKEN_BUDGET_METRICS` | `int` | `500` | NF metrics evidence section |
| `RCA_TOKEN_BUDGET_LOGS` | `int` | `1300` | NF logs evidence section |
| `RCA_TOKEN_BUDGET_TRACES` | `int` | `500` | UE traces evidence section |
| `RCA_LOG_MAX_MESSAGE_CHARS` | `int` | `200` | Max characters per individual log message before truncation. |
| `RCA_MAX_DEVIATIONS_PER_DAG` | `int` | `3` | Max trace deviations per DAG name before truncation. |

---

## Observability

| Variable | Type | Default | Description |
|---|---|---|---|
| `LANGCHAIN_TRACING_V2` | `str` | `"false"` | Set to `"true"` to enable LangSmith tracing. Wires `LANGCHAIN_PROJECT`, `LANGCHAIN_ENDPOINT`, and `LANGSMITH_API_KEY` into the environment automatically. |
| `LANGSMITH_API_KEY` | `str` | `""` | LangSmith API key. Only used when `langchain_tracing_v2 = "true"`. |
| `LANGSMITH_PROJECT` | `str` | `"5g-triage-agent"` | LangSmith project name. |
| `LANGCHAIN_PROJECT` | `str` | `"5g-triage-agent"` | LangChain project name (forwarded to `LANGCHAIN_PROJECT` env var). |
| `LANGCHAIN_ENDPOINT` | `str` | `"https://api.smith.langchain.com"` | LangSmith API endpoint. |
| `ARTIFACTS_DIR` | `str` | `"artifacts"` | Directory for per-incident JSON snapshot artifacts. Relative paths are resolved from CWD at config-load time. Created automatically if absent. |
| `NF_LATENCY_THRESHOLD_SECONDS` | `float` | `1.0` | Latency (seconds) above which an NF is flagged as degraded in `compress_nf_metrics()`. |
| `APP_VERSION` | `str` | `"3.2.0"` | Application version returned in API metadata endpoints. Should match `pyproject.toml`. |

---

## Constraint: `eq_score_metrics_logs` must equal `high_evidence_threshold`

`EvidenceQualityAgent` writes `eq_score_metrics_logs` (default `0.80`) into `state["evidence_quality_score"]`
when metrics and logs are present but traces are absent.
`should_retry()` in `graph.py` uses `high_evidence_threshold` (default `0.80`) to switch between the
strict and relaxed confidence gates.
If these two values diverge, the relaxed gate will never (or always) activate when traces are missing.
