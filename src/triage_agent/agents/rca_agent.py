"""RCAAgent: Root cause analysis using LLM.

The only agent that uses an LLM. Receives infrastructure findings,
NF metrics, NF logs, UE trace deviations, and DAG structure.
Produces root_nf, failure_mode, confidence, evidence_chain.
"""

import json
import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import traceable
from pydantic import BaseModel, Field, SecretStr

from triage_agent.config import get_config
from triage_agent.state import TriageState
from triage_agent.utils import count_tokens

logger = logging.getLogger(__name__)

# --- Pydantic Models ---


class EvidenceItem(BaseModel):
    """Single piece of evidence in the chain."""

    timestamp: str
    source: Literal["infrastructure", "metrics", "logs", "traces"]
    nf: str
    type: str  # log, metric, event, trace_deviation
    content: str
    significance: str


class Hypothesis(BaseModel):
    """Alternative hypothesis for root cause."""

    layer: Literal["infrastructure", "application"]
    nf: str
    failure_mode: str
    confidence: float = Field(ge=0.0, le=1.0)


class RCAOutput(BaseModel):
    """Structured output from RCA LLM analysis."""

    layer: Literal["infrastructure", "application"]
    root_nf: str
    failure_mode: str
    failed_phase: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_chain: list[EvidenceItem]
    alternative_hypotheses: list[Hypothesis]
    reasoning: str


# --- LLM Prompt Template ---

RCA_PROMPT_TEMPLATE = """\
You are a 5G network expert performing root cause analysis for a {procedure_name} failure.

INFRASTRUCTURE FINDINGS (from InfraAgent):
Infrastructure Score: {infra_score} (0.0 = no infra issue, 1.0 = confirmed infra issue)
{infra_findings_json}

PROCEDURE DAGs (reference procedures for this alert):
{dag_json}

APPLICATION EVIDENCE (time window: {time_window}):

METRICS:
{metrics_formatted}

LOGS (annotated with matched DAG phases):
{logs_formatted}

UE TRACE DEVIATIONS (from Memgraph comparison against reference DAG):
{trace_deviations_formatted}

EVIDENCE QUALITY: {evidence_quality_score}

ANALYSIS FRAMEWORK:
1. Layer Determination:
   - If infra_score >= {infra_root_cause_threshold}: Likely infrastructure root cause
   - If infra_score >= {infra_triggered_threshold}: Possible infrastructure-triggered application failure
   - If infra_score < {app_only_threshold}: Likely pure application failure

2. Root Cause Identification:
   - Use temporal precedence (earliest anomaly in time window)
   - Use DAG topology (upstream NFs more likely to be root cause)
   - Correlate infrastructure findings with application symptoms
   - Match log messages against DAG failure_patterns (wildcard matching)

3. Infrastructure vs Application Decision:
   - Infrastructure root cause: Pod-level issues (OOMKill, CrashLoop, resource exhaustion)
   - Application root cause: NF logic errors, protocol failures, data validation errors
   - Infrastructure-triggered application: Infra issue causes cascading app failures

EXAMPLES:
- High infra_score (0.90) + OOMKill event + no app errors before crash
  -> Infrastructure layer, root_nf = pod
- Medium infra_score (0.55) + memory pressure + auth timeout logs
  -> Application layer, root_nf = AUSF (app error, not infra)
- Low infra_score (0.15) + authentication failure logs
  -> Application layer, root_nf = identified from logs

Return ONLY a JSON object:
{{
  "layer": "infrastructure|application",
  "root_nf": "<NF name or 'pod-level' for infrastructure>",
  "failure_mode": "<from DAG failure_patterns or infrastructure event>",
  "failed_phase": "<phase_id where failure occurred, or null for infra>",
  "confidence": <0.0-1.0>,
  "evidence_chain": [
    {{
      "timestamp": "...",
      "source": "infrastructure|metrics|logs|traces",
      "nf": "...",
      "type": "log|metric|event|trace_deviation",
      "content": "...",
      "significance": "..."
    }}
  ],
  "alternative_hypotheses": [
    {{
      "layer": "...",
      "nf": "...",
      "failure_mode": "...",
      "confidence": <0.0-1.0>
    }}
  ],
  "reasoning": "<explanation combining infra findings + app evidence + trace deviations + temporal causality>"
}}
"""


def format_metrics_for_prompt(metrics: dict[str, Any] | None) -> str:
    if not metrics:
        return "No metrics available."
    return json.dumps(metrics, indent=2)


def format_logs_for_prompt(logs: dict[str, Any] | None) -> str:
    if not logs:
        return "No logs available."
    return json.dumps(logs, indent=2)


def format_trace_deviations_for_prompt(deviations: dict[str, list[dict[str, Any]]] | None) -> str:
    if not deviations:
        return "No UE trace deviations available."
    return json.dumps(deviations, indent=2)


# ---------------------------------------------------------------------------
# Evidence compression — DAG and trace deviations (per-agent for infra/metrics/logs)
# ---------------------------------------------------------------------------
#
# count_tokens, compress_nf_metrics, compress_nf_logs live in their respective
# agents (metrics_agent.py, logs_agent.py) and in utils.py.  By the time
# compress_evidence() is called, state["infra_findings"], state["metrics"], and
# state["logs"] are already pre-compressed.  Only DAGs and trace deviations are
# compressed here (they have no dedicated agent-level compression step).



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


def compress_evidence(state: "TriageState") -> dict[str, str]:
    """Format evidence sections for the LLM prompt.

    infra_findings, metrics, and logs are already compressed by their respective
    agents before being written to state.  Only DAGs and trace_deviations are
    compressed here (they have no dedicated agent-level step).

    Returns a dict keyed by RCA_PROMPT_TEMPLATE placeholders.
    """
    cfg = get_config()
    compressed_dags = compress_dag(state.get("dags"), cfg.rca_token_budget_dag)
    compressed_traces = compress_trace_deviations(
        state.get("trace_deviations"), cfg.rca_token_budget_traces
    )
    return {
        "infra_findings_json": json.dumps(state.get("infra_findings") or {}, indent=2),
        "dag_json": json.dumps(compressed_dags, indent=2),
        "metrics_formatted": format_metrics_for_prompt(state.get("metrics")),
        "logs_formatted": format_logs_for_prompt(state.get("logs")),
        "trace_deviations_formatted": format_trace_deviations_for_prompt(compressed_traces),
    }


def create_llm(
    provider: str,
    model: str,
    api_key: str,
    timeout: int,
    base_url: str = "",
) -> Any:
    """Factory: construct the appropriate LangChain chat model.

    Args:
        provider: One of "openai", "anthropic", "local"
        model: Model name string (provider-specific)
        api_key: API key; empty string allowed for local provider
        timeout: Request timeout in seconds
        base_url: Only used for "local" provider

    Returns:
        A LangChain chat model with .invoke() method

    Raises:
        ImportError: If provider == "anthropic" and langchain-anthropic is not installed
        ValueError: If provider == "local" and base_url is empty, or unknown provider
    """
    temperature = get_config().llm_temperature
    if provider == "openai":
        return ChatOpenAI(
            model=model,
            api_key=SecretStr(api_key) if api_key else None,
            temperature=temperature,
            timeout=timeout,
        )
    elif provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic  # deferred: optional dependency
        except ImportError as e:
            raise ImportError(
                "langchain-anthropic is required for the 'anthropic' provider. "
                "Install it with: pip install triage-agent[anthropic]"
            ) from e
        return ChatAnthropic(  # type: ignore[return-value]
            model=model,
            api_key=SecretStr(api_key) if api_key else None,  # type: ignore[arg-type]
            temperature=temperature,
            timeout=timeout,
        )
    elif provider == "local":
        if not base_url:
            raise ValueError(
                "llm_base_url must be set when llm_provider is 'local'. "
                "Set LLM_BASE_URL env var to the OpenAI-compatible endpoint, "
                "e.g. http://vllm-service.5g-core:8080/v1"
            )
        return ChatOpenAI(
            model=model,
            api_key=SecretStr(api_key) if api_key else SecretStr("local"),
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
        )
    else:
        raise ValueError(f"Unsupported llm_provider: '{provider}'")


def llm_analyze_evidence(prompt: str, timeout: int | None = None) -> dict[str, Any]:
    """Call LLM with the RCA prompt. Returns parsed JSON response.

    Args:
        prompt: The formatted RCA prompt
        timeout: Optional timeout in seconds (defaults to config.llm_timeout)

    Returns:
        Parsed JSON dict from LLM response

    Raises:
        TimeoutError: If LLM request exceeds timeout
        ValueError: If LLM response is not valid JSON
    """
    config = get_config()
    timeout_val = timeout or config.llm_timeout

    # Initialize LLM client via factory (supports openai / anthropic / local)
    llm = create_llm(
        provider=config.llm_provider,
        model=config.llm_model,
        api_key=config.llm_api_key,
        timeout=timeout_val,
        base_url=config.llm_base_url,
    )

    messages = [
        SystemMessage(
            content="You are a 5G network expert performing root cause analysis. "
            "Always respond with valid JSON only, no markdown formatting."
        ),
        HumanMessage(content=prompt),
    ]

    try:
        response = llm.invoke(messages)

        # Handle response content which might be str or list
        if isinstance(response.content, str):
            response_text = response.content.strip()
        else:
            # If it's a list, join it
            response_text = "".join(str(item) for item in response.content).strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]

        response_text = response_text.strip()

        # Parse JSON
        analysis: dict[str, Any] = json.loads(response_text)
        return analysis

    except Exception as e:
        # Convert any timeout-related exception to TimeoutError
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            raise TimeoutError(f"LLM request timed out after {timeout_val}s") from e
        raise


def generate_final_report(state: TriageState) -> dict[str, Any]:
    """Generate final RCA report from state.

    Args:
        state: Current triage state with RCA results

    Returns:
        Final report dictionary with summary and details
    """
    procedure_names = state.get("procedure_names") or []
    return {
        "incident_id": state["incident_id"],
        "procedure_name": ", ".join(procedure_names) if procedure_names else "unknown",
        "layer": state.get("layer", "unknown"),
        "root_nf": state.get("root_nf", "unknown"),
        "failure_mode": state.get("failure_mode", "unknown"),
        "confidence": state.get("confidence", 0.0),
        "evidence_chain": state.get("evidence_chain", []),
        "summary": (
            f"{state.get('layer', 'unknown').title()} layer failure: "
            f"{state.get('failure_mode', 'unknown')} in {state.get('root_nf', 'unknown')} "
            f"(confidence: {state.get('confidence', 0.0):.2f})"
        ),
        "infra_score": state.get("infra_score", 0.0),
        "evidence_quality_score": state.get("evidence_quality_score", 0.0),
    }


def identify_evidence_gaps(state: TriageState) -> list[str]:
    """Identify what additional evidence is needed for higher confidence.

    Args:
        state: Current triage state

    Returns:
        List of evidence gap descriptions
    """
    gaps = []

    # Check for missing data sources
    if not state.get("metrics") or state.get("metrics") == {}:
        gaps.append("NF metrics data needed")

    if not state.get("logs") or state.get("logs") == {}:
        gaps.append("NF logs data needed")

    if not state.get("trace_deviations"):
        gaps.append("UE trace analysis needed")

    cfg = get_config()
    # Check evidence quality
    if state.get("evidence_quality_score", 0.0) < cfg.evidence_gap_quality_threshold:
        gaps.append("Overall evidence quality too low")

    # Check infrastructure findings
    if state.get("infra_score", 0.0) > cfg.infra_triggered_threshold and not state.get("infra_findings"):
        gaps.append("Detailed infrastructure findings needed")

    # If no specific gaps identified but confidence is low
    if not gaps and state.get("confidence", 0.0) < cfg.evidence_gap_confidence_threshold:
        gaps.append("Additional temporal analysis needed")
        gaps.append("Cross-correlation of events needed")

    return gaps


@traceable(name="rca_agent_first_attempt")
def rca_agent_first_attempt(state: TriageState) -> TriageState:
    """RCAAgent first attempt. Uses LLM for analysis.

    Evidence is compressed to fit within the LLM's context window before
    the prompt is built.

    Args:
        state: Current triage state with all evidence collected

    Returns:
        Delta dict with RCA results for LangGraph state merge
    """
    _cfg = get_config()
    evidence = compress_evidence(state)
    prompt = RCA_PROMPT_TEMPLATE.format(
        procedure_name=", ".join(state.get("procedure_names") or ["unknown"]),
        infra_score=state.get("infra_score", 0.0),
        time_window="alert_time - 5min to alert_time + 60s",
        evidence_quality_score=state.get("evidence_quality_score", 0.0),
        infra_root_cause_threshold=_cfg.infra_root_cause_threshold,
        infra_triggered_threshold=_cfg.infra_triggered_threshold,
        app_only_threshold=_cfg.app_only_threshold,
        **evidence,
    )

    analysis = llm_analyze_evidence(prompt)

    # Update state with analysis results
    state["root_nf"] = analysis["root_nf"]
    state["failure_mode"] = analysis["failure_mode"]
    state["confidence"] = analysis["confidence"]
    state["evidence_chain"] = analysis.get("evidence_chain", [])
    state["layer"] = analysis.get("layer", "application")

    # Decision logic: determine if more evidence is needed
    cfg = get_config()
    min_confidence = cfg.min_confidence_default
    if state.get("evidence_quality_score", 0.0) >= cfg.high_evidence_threshold:
        min_confidence = cfg.min_confidence_relaxed

    if state["confidence"] >= min_confidence:
        state["needs_more_evidence"] = False
        state["final_report"] = generate_final_report(state)
    else:
        state["needs_more_evidence"] = True
        state["evidence_gaps"] = identify_evidence_gaps(state)

    # Mark second attempt complete if this was a retry
    second_attempt_complete = state.get("attempt_count", 1) > 1

    return {
        "root_nf": state["root_nf"],
        "failure_mode": state["failure_mode"],
        "confidence": state["confidence"],
        "evidence_chain": state.get("evidence_chain", []),
        "layer": state["layer"],
        "needs_more_evidence": state["needs_more_evidence"],
        "evidence_gaps": state.get("evidence_gaps"),
        "second_attempt_complete": second_attempt_complete,
        "final_report": state.get("final_report"),
    }
