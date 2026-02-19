"""Configuration management for TriageAgent."""

from functools import lru_cache
from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class TriageAgentConfig(BaseSettings):
    """Configuration loaded from environment variables.

    All fields may be overridden via environment variable (case-insensitive)
    or via a `.env` file in the working directory.  List-typed fields accept
    a JSON array string, e.g. ``KNOWN_NFS='["amf","smf","custom-nf"]'``.
    """

    # -------------------------------------------------------------------------
    # infra_config — Database and Cluster Infrastructure
    # -------------------------------------------------------------------------

    memgraph_host: str = "localhost"
    memgraph_port: int = 7687

    # Max concurrent Bolt connections; generous default for a sidecar Memgraph.
    memgraph_pool_size: int = 10

    # Retry attempts for Cypher queries on ServiceUnavailable/TransientError.
    # Uses exponential backoff: 2**attempt seconds per retry.
    memgraph_max_retries: int = 3

    # Known 5G NF names used for alert label extraction and pod-name fallback.
    # A new NF not in this list is silently ignored during alert parsing.
    # JSON list via env: KNOWN_NFS='["amf","smf","custom-nf"]'
    known_nfs: list[str] = [
        "amf", "smf", "upf", "nrf", "ausf", "udm", "udr", "pcf", "nssf",
    ]

    # Kubernetes / 5G Core namespace.
    # Env var: CORE_NAMESPACE — K8s namespace label used in Prometheus/Loki queries.
    core_namespace: str = "5g-core"

    # -------------------------------------------------------------------------
    # api_config — Service Connectivity and Timeouts
    # -------------------------------------------------------------------------

    prometheus_url: str = (
        "http://kube-prom-kube-prometheus-prometheus.monitoring:9090"
    )
    loki_url: str = "http://loki.monitoring:3100"

    # Seconds for HTTP requests to Prometheus/Loki.  Aggressive by design;
    # on a busy cluster this triggers graceful fallback paths.
    mcp_timeout: float = 3.0

    # Retry attempts on HTTP 429 rate-limit responses from Prometheus.
    # Uses exponential backoff: 2**attempt seconds (1 s, 2 s, 4 s).
    prometheus_max_retries: int = 3

    # CORS origins for the FastAPI webhook.  Restrict to Alertmanager IP in
    # production.  JSON list via env: CORS_ALLOW_ORIGINS='["http://am:9093"]'
    cors_allow_origins: list[str] = ["*"]

    # Host/port for the uvicorn webhook server.
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # -------------------------------------------------------------------------
    # model_config — LLM / Model Parameters
    # -------------------------------------------------------------------------

    llm_api_key: str = ""  # Required in production for openai/anthropic providers.
    # Model filename for local vLLM/Ollama or model name for cloud providers.
    llm_model: str = "qwen3-4b-instruct-2507.Q4_K_M.gguf"
    # Maximum seconds to wait for an LLM response before degraded-mode fallback.
    llm_timeout: int = 300
    # Env var: LLM_PROVIDER — selects LLM backend.
    # "openai": ChatOpenAI using llm_api_key + llm_model
    # "anthropic": ChatAnthropic using llm_api_key + llm_model
    # "local": ChatOpenAI with base_url for in-cluster vLLM/Ollama
    llm_provider: Literal["openai", "anthropic", "local"] = "local"
    # Env var: LLM_BASE_URL — OpenAI-compatible base URL for the local provider.
    # Defaults to the in-cluster Qwen3-4b KServe ClusterIP service (port 80).
    llm_base_url: str = "http://qwen3-4b.ml-serving.svc.cluster.local/v1"
    # Sampling temperature.  Near-zero maximises determinism for structured JSON output.
    llm_temperature: float = 0.1

    # -------------------------------------------------------------------------
    # agent_config — Pipeline Flow / Retry Logic
    # -------------------------------------------------------------------------

    # Hard limit on RCA retries.  First attempt + (max_attempts - 1) retries.
    max_attempts: int = 2

    # Confidence gate: RCA requests retry when confidence < min_confidence_default.
    min_confidence_default: float = 0.70
    # Relaxed gate applied when evidence_quality_score >= high_evidence_threshold.
    min_confidence_relaxed: float = 0.65
    # Evidence quality score at which min_confidence_relaxed activates.
    high_evidence_threshold: float = 0.80

    # Shared alert query window used by InfraAgent, NfLogsAgent, and UeTracesAgent.
    alert_lookback_seconds: int = 300   # 5 minutes before alert start
    alert_lookahead_seconds: int = 60   # 60 seconds after alert start

    # IMSI discovery: narrow window around alert time.
    imsi_discovery_window_seconds: int = 30
    # Per-IMSI trace: wider lookback to capture the full signalling procedure.
    imsi_trace_lookback_seconds: int = 120

    # ITU-T E.212 defines IMSIs as up to 15 digits.  Adjust only for private
    # networks that use shorter identifiers.
    imsi_digit_length: int = 15

    # -------------------------------------------------------------------------
    # query_config — PromQL / LogQL Parameters
    # -------------------------------------------------------------------------

    # Rolling window for pod restart count queries.
    promql_restart_window: str = "1h"
    # Rolling window for OOM kill queries.
    promql_oom_window: str = "5m"
    # rate() window for infra-level CPU usage queries.
    promql_cpu_rate_window_infra: str = "2m"
    # rate() window for per-NF HTTP error rate queries.
    promql_error_rate_window: str = "1m"
    # Histogram quantile for per-NF latency queries (e.g. 0.95 = p95).
    promql_latency_quantile: float = 0.95
    # rate() window for per-NF CPU usage queries.
    promql_cpu_rate_window_nf: str = "5m"
    # Default resolution step for Prometheus range queries.
    promql_range_step: str = "15s"
    # Maximum log lines returned per LogQL query across all Loki paths.
    # Truncation is silent; raise this if high-volume incidents are missing logs.
    loki_query_limit: int = 1000

    # -------------------------------------------------------------------------
    # scoring_config — Thresholds and Weights
    # -------------------------------------------------------------------------

    # --- Infra scoring weights (must sum to 1.0) ---
    # Pod Reliability (Restarts)
    infra_weight_restarts: float = 0.35
    # Critical Errors (OOM kills)
    infra_weight_oom: float = 0.25
    # Pod Health Status
    infra_weight_pod_status: float = 0.20
    # Resource Saturation (CPU / memory)
    infra_weight_resources: float = 0.20

    # --- Restart breakpoints ---
    # Restart count strictly above this gives factor 1.0 (maximum).
    restart_threshold_critical: int = 5
    # Restart count >= this gives restart_factor_high.
    restart_threshold_high: int = 3
    # Factor applied when restarts >= restart_threshold_high (and <= restart_threshold_critical).
    restart_factor_high: float = 0.7
    # Factor applied when restarts >= 1 but < restart_threshold_high.
    restart_factor_low: float = 0.4

    # --- Resource saturation thresholds ---
    # Memory usage % above which resource_factor = 1.0.
    memory_saturation_pct: float = 90.0
    # CPU usage (cores) above which resource_factor = cpu_saturation_factor.
    cpu_saturation_cores: float = 1.0
    # Resource factor when CPU exceeds cpu_saturation_cores.
    cpu_saturation_factor: float = 0.8
    # Status factor applied to Pending pods (Failed/Unknown → 1.0).
    pod_pending_factor: float = 0.6

    # --- RCA layer determination thresholds ---
    # Used in LLM system prompt AND degraded_mode_analysis() fallback.
    # infra_score >= this → infrastructure root cause.
    infra_root_cause_threshold: float = 0.80
    # infra_score >= this → possible infrastructure-triggered application failure.
    infra_triggered_threshold: float = 0.60
    # infra_score < this → likely pure application failure.
    app_only_threshold: float = 0.30

    # --- Degraded mode confidence values ---
    # Lower than normal LLM output to signal reduced reliability.
    degraded_conf_infra_generic: float = 0.50
    degraded_conf_infra_specific: float = 0.60   # OOMKilled / CrashLoopBackOff
    degraded_conf_app_unknown: float = 0.40
    degraded_conf_app_pattern_match: float = 0.50  # timeout / auth-failure keyword

    # --- Evidence gap thresholds ---
    # Evidence quality below this → "Overall evidence quality too low" gap.
    evidence_gap_quality_threshold: float = 0.50
    # Confidence below this (no specific gaps found) → generic gap flagged.
    evidence_gap_confidence_threshold: float = 0.70

    # --- Evidence quality scores ---
    # NOTE: eq_score_metrics_logs must equal high_evidence_threshold for the
    # cross-file relaxed confidence gate to behave correctly.
    eq_score_all_sources: float = 0.95    # metrics + logs + traces
    eq_score_traces_plus_one: float = 0.85  # traces + one other source
    eq_score_metrics_logs: float = 0.80   # metrics + logs (no traces)
    eq_score_traces_only: float = 0.50
    eq_score_metrics_only: float = 0.40
    eq_score_logs_only: float = 0.35
    eq_score_no_evidence: float = 0.10    # sentinel; not 0.0

    # -------------------------------------------------------------------------
    # observability_config — Tracing and Monitoring
    # -------------------------------------------------------------------------

    langsmith_project: str = "5g-triage-agent"
    langsmith_api_key: str = ""

    # Application version returned in API metadata endpoints.
    # Should match pyproject.toml; update on each release.
    app_version: str = "3.2.0"

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
        "env_file": ".env",           # load from .env if present
        "env_file_encoding": "utf-8",
        "extra": "ignore",            # ignore unknown keys in .env file
    }

    @field_validator("memgraph_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port is positive."""
        if v <= 0:
            raise ValueError("memgraph_port must be positive")
        return v

    @field_validator("prometheus_url", "loki_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL starts with http:// or https://."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @property
    def memgraph_uri(self) -> str:
        """Bolt connection URI for Memgraph."""
        return f"bolt://{self.memgraph_host}:{self.memgraph_port}"


@lru_cache(maxsize=1)
def get_config() -> TriageAgentConfig:
    """Get singleton configuration instance."""
    return TriageAgentConfig()


def get_config_dict() -> dict[str, Any]:
    """Get configuration as dictionary (for testing)."""
    return get_config().model_dump()
