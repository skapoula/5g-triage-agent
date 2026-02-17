"""RCAAgent: Root cause analysis using LLM.

The only agent that uses an LLM. Receives infrastructure findings,
NF metrics, NF logs, UE trace deviations, and DAG structure.
Produces root_nf, failure_mode, confidence, evidence_chain.
"""

import json
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import traceable
from pydantic import BaseModel, Field, SecretStr

from triage_agent.config import get_config
from triage_agent.state import TriageState

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

PROCEDURE DAG:
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
   - If infra_score >= 0.80: Likely infrastructure root cause
   - If infra_score >= 0.60: Possible infrastructure-triggered application failure
   - If infra_score < 0.30: Likely pure application failure

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


def format_trace_deviations_for_prompt(deviations: list[dict[str, Any]] | None) -> str:
    if not deviations:
        return "No UE trace deviations available."
    return json.dumps(deviations, indent=2)


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

    # Initialize LLM client
    llm = ChatOpenAI(
        model=config.llm_model,
        api_key=SecretStr(config.llm_api_key) if config.llm_api_key else None,
        temperature=0.1,  # Low temperature for deterministic analysis
        timeout=timeout_val,
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
    return {
        "incident_id": state["incident_id"],
        "procedure_name": state.get("procedure_name", "unknown"),
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
        "degraded_mode": state.get("degraded_mode", False),
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

    if not state.get("trace_deviations") or state.get("trace_deviations") == []:
        gaps.append("UE trace analysis needed")

    # Check evidence quality
    if state.get("evidence_quality_score", 0.0) < 0.50:
        gaps.append("Overall evidence quality too low")

    # Check infrastructure findings
    if state.get("infra_score", 0.0) > 0.60 and not state.get("infra_findings"):
        gaps.append("Detailed infrastructure findings needed")

    # If no specific gaps identified but confidence is low
    if not gaps and state.get("confidence", 0.0) < 0.70:
        gaps.append("Additional temporal analysis needed")
        gaps.append("Cross-correlation of events needed")

    return gaps


def degraded_mode_analysis(state: TriageState) -> dict[str, Any]:
    """Rule-based fallback analysis when LLM times out.

    Uses heuristics to provide basic RCA without LLM.

    Args:
        state: Current triage state

    Returns:
        Analysis dict with lower confidence scores
    """
    # Heuristic: High infra_score -> infrastructure layer
    infra_score = state.get("infra_score", 0.0)

    if infra_score >= 0.80:
        # Infrastructure root cause
        root_nf = "pod-level"
        failure_mode = "infrastructure_issue"
        layer = "infrastructure"
        confidence = 0.50  # Lower confidence for degraded mode

        # Try to extract specifics from infra_findings
        infra_findings = state.get("infra_findings")
        if infra_findings and isinstance(infra_findings, dict):
            if "OOMKilled" in str(infra_findings):
                failure_mode = "OOMKilled"
                confidence = 0.60
            elif "CrashLoop" in str(infra_findings):
                failure_mode = "CrashLoopBackOff"
                confidence = 0.60

    else:
        # Application layer - try to extract from logs
        layer = "application"
        root_nf = "unknown"
        failure_mode = "undetermined"
        confidence = 0.40

        logs = state.get("logs")
        if logs and isinstance(logs, dict):
            # Simple keyword matching
            for nf_name, log_entries in logs.items():
                if isinstance(log_entries, list) and log_entries:
                    for entry in log_entries:
                        message = str(entry.get("message", "")).lower()
                        if "timeout" in message:
                            root_nf = nf_name
                            failure_mode = "timeout"
                            confidence = 0.50
                            break
                        elif "auth" in message and "fail" in message:
                            root_nf = nf_name
                            failure_mode = "authentication_failure"
                            confidence = 0.50
                            break
                if root_nf != "unknown":
                    break

    return {
        "layer": layer,
        "root_nf": root_nf,
        "failure_mode": failure_mode,
        "confidence": confidence,
        "evidence_chain": [],
        "alternative_hypotheses": [],
        "reasoning": "Degraded mode: rule-based analysis due to LLM timeout",
    }


@traceable(name="rca_agent_first_attempt")
def rca_agent_first_attempt(state: TriageState) -> TriageState:
    """RCAAgent first attempt. Uses LLM for analysis.

    Handles LLM timeout with degraded mode fallback.

    Args:
        state: Current triage state with all evidence collected

    Returns:
        Updated state with RCA results
    """
    prompt = RCA_PROMPT_TEMPLATE.format(
        procedure_name=state.get("procedure_name", "unknown"),
        infra_score=state.get("infra_score", 0.0),
        infra_findings_json=json.dumps(state.get("infra_findings"), indent=2),
        dag_json=json.dumps(state.get("dag"), indent=2),
        time_window="alert_time - 5min to alert_time + 60s",
        metrics_formatted=format_metrics_for_prompt(state.get("metrics")),
        logs_formatted=format_logs_for_prompt(state.get("logs")),
        trace_deviations_formatted=format_trace_deviations_for_prompt(
            state.get("trace_deviations")
        ),
        evidence_quality_score=state.get("evidence_quality_score", 0.0),
    )

    # Try LLM analysis with timeout handling
    try:
        analysis = llm_analyze_evidence(prompt)
        state["degraded_mode"] = False
        state["degraded_reason"] = None

    except TimeoutError:
        # LLM timed out - use degraded mode
        config = get_config()
        state["degraded_mode"] = True
        state["degraded_reason"] = f"LLM timeout after {config.llm_timeout}s"
        analysis = degraded_mode_analysis(state)

    # Update state with analysis results
    state["root_nf"] = analysis["root_nf"]
    state["failure_mode"] = analysis["failure_mode"]
    state["confidence"] = analysis["confidence"]
    state["evidence_chain"] = analysis.get("evidence_chain", [])
    state["layer"] = analysis.get("layer", "application")

    # Decision logic: determine if more evidence is needed
    min_confidence = 0.70
    if state.get("evidence_quality_score", 0.0) >= 0.80:
        min_confidence = 0.65

    if state["confidence"] >= min_confidence:
        state["needs_more_evidence"] = False
        state["final_report"] = generate_final_report(state)
    else:
        state["needs_more_evidence"] = True
        state["evidence_gaps"] = identify_evidence_gaps(state)

    return state
