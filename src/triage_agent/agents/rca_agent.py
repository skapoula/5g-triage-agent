"""RCAAgent: Root cause analysis using LLM.

The only agent that uses an LLM. Receives infrastructure findings,
NF metrics, NF logs, UE trace deviations, and DAG structure.
Produces root_nf, failure_mode, confidence, evidence_chain.
"""

import json

from triage_agent.state import TriageState

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
- High infra_score (0.90) + OOMKill event + no app errors before crash -> Infrastructure layer, root_nf = pod
- Medium infra_score (0.55) + memory pressure + auth timeout logs -> Application layer, root_nf = AUSF (app error, not infra)
- Low infra_score (0.15) + authentication failure logs -> Application layer, root_nf = identified from logs

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


def format_metrics_for_prompt(metrics: dict | None) -> str:
    if not metrics:
        return "No metrics available."
    return json.dumps(metrics, indent=2)


def format_logs_for_prompt(logs: dict | None) -> str:
    if not logs:
        return "No logs available."
    return json.dumps(logs, indent=2)


def format_trace_deviations_for_prompt(deviations: list | None) -> str:
    if not deviations:
        return "No UE trace deviations available."
    return json.dumps(deviations, indent=2)


def llm_analyze_evidence(prompt: str) -> dict:
    """Call LLM with the RCA prompt. Returns parsed JSON response."""
    # TODO: wire up LLM client (LangChain)
    raise NotImplementedError


def generate_final_report(state: TriageState) -> dict:
    raise NotImplementedError


def identify_evidence_gaps(state: TriageState) -> list:
    raise NotImplementedError


def rca_agent_first_attempt(state: TriageState) -> TriageState:
    """RCAAgent first attempt. Uses LLM for analysis."""
    prompt = RCA_PROMPT_TEMPLATE.format(
        procedure_name=state.get("procedure_name", "unknown"),
        infra_score=state.get("infra_score", 0.0),
        infra_findings_json=json.dumps(state.get("infra_findings"), indent=2),
        dag_json=json.dumps(state.get("dag"), indent=2),
        time_window="alert_time - 5min to alert_time + 60s",
        metrics_formatted=format_metrics_for_prompt(state.get("metrics")),
        logs_formatted=format_logs_for_prompt(state.get("logs")),
        trace_deviations_formatted=format_trace_deviations_for_prompt(state.get("trace_deviations")),
        evidence_quality_score=state.get("evidence_quality_score", 0.0),
    )

    analysis = llm_analyze_evidence(prompt)

    state["root_nf"] = analysis["root_nf"]
    state["failure_mode"] = analysis["failure_mode"]
    state["confidence"] = analysis["confidence"]
    state["evidence_chain"] = analysis["evidence_chain"]
    state["layer"] = analysis.get("layer", "application")

    # Decision logic
    min_confidence = 0.70
    if state["evidence_quality_score"] >= 0.80:
        min_confidence = 0.65

    if state["confidence"] >= min_confidence:
        state["needs_more_evidence"] = False
        state["final_report"] = generate_final_report(state)
    else:
        state["needs_more_evidence"] = True
        state["evidence_gaps"] = identify_evidence_gaps(state)

    return state
