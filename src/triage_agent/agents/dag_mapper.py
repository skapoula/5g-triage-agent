"""DagMapper: Maps alert to one or more 3GPP procedure DAGs.

No LLM. Uses a priority cascade to map alert labels/annotations to known
3GPP procedures, then fetches each procedure's reference DAG from Memgraph.

Mapping cascade:
    1. exact_match   — alert label 'procedure' names a known DAG directly
    2. keyword_match — alertname or description contains a procedure keyword
    3. nf_default    — alert's 'nf' label maps to known default procedures
    4. generic_fallback — all known procedures (low confidence)
"""

import logging
from typing import Any

from langsmith import traceable

from triage_agent.memgraph.connection import get_memgraph
from triage_agent.state import TriageState

logger = logging.getLogger(__name__)

KNOWN_DAGS: list[str] = [
    "registration_general",
    "authentication_5g_aka",
    "pdu_session_establishment",
]

KEYWORD_MAP: dict[str, list[str]] = {
    "registration": ["registration_general"],
    "auth": ["authentication_5g_aka", "registration_general"],
    "pdu": ["pdu_session_establishment"],
    "session": ["pdu_session_establishment"],
}

NF_DEFAULT_MAP: dict[str, list[str]] = {
    "amf":  ["registration_general", "authentication_5g_aka"],
    "ausf": ["authentication_5g_aka"],
    "udm":  ["registration_general", "authentication_5g_aka"],
    "smf":  ["pdu_session_establishment"],
    "upf":  ["pdu_session_establishment"],
    "nrf":  ["registration_general"],
    "pcf":  ["registration_general"],
    "udr":  ["registration_general"],
    "nssf": ["registration_general"],
}


def map_alert_to_procedures(
    alert: dict[str, Any],
) -> tuple[list[str], str, float]:
    """Map alert to a list of DAG names using the priority cascade.

    Returns:
        (dag_names, mapping_method, mapping_confidence)
    """
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    # 1. exact_match
    procedure_label = labels.get("procedure", "").strip().lower()
    if procedure_label in KNOWN_DAGS:
        return [procedure_label], "exact_match", 1.0

    # 2. keyword_match — scan alertname + description
    search_text = " ".join([
        labels.get("alertname", ""),
        annotations.get("description", ""),
        annotations.get("summary", ""),
    ]).lower()

    matched: list[str] = []
    for keyword, dag_names in KEYWORD_MAP.items():
        if keyword in search_text:
            for name in dag_names:
                if name not in matched:
                    matched.append(name)

    if matched:
        return matched, "keyword_match", 0.8

    # 3. nf_default — use NF label
    nf_label = labels.get("nf", "").strip().lower()
    if nf_label in NF_DEFAULT_MAP:
        return NF_DEFAULT_MAP[nf_label], "nf_default", 0.6

    # 4. generic_fallback
    return KNOWN_DAGS, "generic_fallback", 0.3


def compute_nf_union(dags: list[dict[str, Any]]) -> list[str]:
    """Compute deduplicated union of all_nfs across a list of DAGs."""
    seen: set[str] = set()
    result: list[str] = []
    for dag in dags:
        for nf in dag.get("all_nfs", []):
            if nf not in seen:
                seen.add(nf)
                result.append(nf)
    return result


@traceable(name="DagMapper")
def dag_mapper(state: TriageState) -> dict[str, Any]:
    """DagMapper entry point. Deterministic, no LLM.

    Maps alert to procedure DAGs and computes NF union for downstream agents.
    Gracefully degrades (empty dags) if Memgraph is unreachable.
    """
    alert = state["alert"]
    dag_names, method, confidence = map_alert_to_procedures(alert)

    dags: list[dict[str, Any]] = []
    loaded_dag_ids: list[str] = []

    try:
        conn = get_memgraph()
        for name in dag_names:
            dag = conn.load_reference_dag(name)
            if dag is not None:
                dags.append(dag)
                loaded_dag_ids.append(name)
            else:
                logger.warning("DAG not found in Memgraph: %s", name)
    except Exception:
        logger.warning(
            "Memgraph unavailable in dag_mapper, proceeding with empty DAGs",
            exc_info=True,
        )
        dags = []
        loaded_dag_ids = []

    return {
        "procedure_names": loaded_dag_ids,
        "dag_ids": loaded_dag_ids,
        "dags": dags,
        "nf_union": compute_nf_union(dags),
        "mapping_confidence": confidence,
        "mapping_method": method,
    }
