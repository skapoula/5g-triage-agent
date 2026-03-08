"""Tests for DagMapper alert-to-procedure mapping."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from triage_agent.state import TriageState

# --- Pure function tests (no Memgraph) ---

class TestMapAlertToProcedures:
    """Tests for the alert→procedure mapping cascade."""

    def test_exact_match_via_procedure_label(self) -> None:
        """Alert label 'procedure' that names a known DAG → exact_match."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"procedure": "Registration_General", "nf": "amf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert dag_names == ["Registration_General"]
        assert method == "exact_match"
        assert confidence == pytest.approx(1.0)

    def test_keyword_match_registration_in_alertname(self) -> None:
        """'registration' in alertname → keyword_match for Registration_General."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": ""},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert "Registration_General" in dag_names
        assert method == "keyword_match"
        assert confidence == pytest.approx(0.8)

    def test_keyword_match_auth_in_description(self) -> None:
        """'auth' in description → keyword_match for Authentication_5G_AKA."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "NfDown"},
            "annotations": {"description": "AUSF authentication timeout"},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert "Authentication_5G_AKA" in dag_names
        assert method == "keyword_match"

    def test_nf_default_amf(self) -> None:
        """AMF alert with no keywords → nf_default returns registration + auth."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "AmfDown", "nf": "amf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert set(dag_names) == {"Registration_General", "Authentication_5G_AKA"}
        assert method == "nf_default"
        assert confidence == pytest.approx(0.6)

    def test_nf_default_smf(self) -> None:
        """SMF alert → nf_default returns PDU_Session_Establishment only."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "SmfError", "nf": "smf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert dag_names == ["PDU_Session_Establishment"]
        assert method == "nf_default"

    def test_generic_fallback_unknown_nf_no_keywords(self) -> None:
        """Alert with unrecognised NF and no keywords → generic_fallback."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "SomeGenericAlert", "nf": "unknown-nf"},
            "annotations": {},
        }
        dag_names, method, confidence = map_alert_to_procedures(alert)

        assert set(dag_names) == {"Registration_General", "Authentication_5G_AKA", "PDU_Session_Establishment"}
        assert method == "generic_fallback"
        assert confidence == pytest.approx(0.3)

    def test_exact_match_unknown_procedure_label_falls_through(self) -> None:
        """A 'procedure' label that is NOT a known DAG name falls through to next method."""
        from triage_agent.agents.dag_mapper import map_alert_to_procedures

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": "amf", "procedure": "unknown_dag"},
            "annotations": {},
        }
        _, method, _ = map_alert_to_procedures(alert)

        assert method != "exact_match"


class TestComputeNfUnion:
    """Tests for NF union computation across multiple DAGs."""

    def test_union_deduplicates_nfs(self) -> None:
        """NFs appearing in multiple DAGs appear only once in the union."""
        from triage_agent.agents.dag_mapper import compute_nf_union

        dags = [
            {"all_nfs": ["AMF", "AUSF", "UDM"]},
            {"all_nfs": ["AMF", "SMF", "UPF"]},
        ]
        union = compute_nf_union(dags)

        assert sorted(union) == sorted(["AMF", "AUSF", "UDM", "SMF", "UPF"])

    def test_empty_dags_returns_empty_list(self) -> None:
        """No DAGs → empty NF union."""
        from triage_agent.agents.dag_mapper import compute_nf_union

        assert compute_nf_union([]) == []


# --- Agent entry point tests (Memgraph mocked) ---

class TestDagMapperAgent:
    """Tests for the dag_mapper agent entry point."""

    def _make_state(self, alert: dict[str, Any]) -> TriageState:
        return TriageState(
            alert=alert,
            incident_id="test-001",
            infra_checked=False,
            infra_score=0.0,
            infra_findings=None,
            procedure_names=None,
            dag_ids=None,
            dags=None,
            nf_union=None,
            mapping_confidence=0.0,
            mapping_method="",
            metrics=None,
            logs=None,
            discovered_imsis=None,
            traces_ready=False,
            trace_deviations=None,
            evidence_quality_score=0.0,
            root_nf=None,
            failure_mode=None,
            layer="",
            confidence=0.0,
            evidence_chain=[],
            attempt_count=1,
            max_attempts=2,
            needs_more_evidence=False,
            evidence_gaps=None,
            compressed_evidence=None,
            final_report=None,
        )

    def test_returns_delta_dict_with_all_required_keys(self, mock_memgraph: MagicMock) -> None:
        """dag_mapper returns a delta dict with procedure_names, dags, nf_union, etc."""
        from triage_agent.agents.dag_mapper import dag_mapper

        mock_memgraph.load_reference_dag.return_value = {
            "name": "Registration_General",
            "procedure": "registration",
            "all_nfs": ["AMF", "AUSF"],
            "phases": [],
        }

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": "amf"},
            "annotations": {},
            "startsAt": "2026-02-15T10:00:00Z",
        }

        with patch("triage_agent.agents.dag_mapper.get_memgraph", return_value=mock_memgraph):
            result = dag_mapper(self._make_state(alert))

        required_keys = {"procedure_names", "dag_ids", "dags", "nf_union", "mapping_confidence", "mapping_method"}
        assert required_keys.issubset(result.keys())

    def test_memgraph_failure_returns_empty_dags(self, mock_memgraph: MagicMock) -> None:
        """If Memgraph raises, dag_mapper returns empty dags (degraded mode)."""
        from triage_agent.agents.dag_mapper import dag_mapper

        mock_memgraph.load_reference_dag.side_effect = Exception("Memgraph unavailable")

        alert = {
            "labels": {"alertname": "RegistrationFailures", "nf": "amf"},
            "annotations": {},
            "startsAt": "2026-02-15T10:00:00Z",
        }

        with patch("triage_agent.agents.dag_mapper.get_memgraph", return_value=mock_memgraph):
            result = dag_mapper(self._make_state(alert))

        assert result["dags"] == []
        assert result["nf_union"] == []

    def test_nf_union_is_deduplicated_across_matched_dags(self, mock_memgraph: MagicMock) -> None:
        """When multiple DAGs are matched, nf_union contains no duplicates."""
        from triage_agent.agents.dag_mapper import dag_mapper

        def load_dag(name: str) -> dict[str, Any]:
            return {
                "name": name,
                "procedure": name,
                "all_nfs": ["AMF", "AUSF"] if "registration" in name else ["AMF", "SMF"],
                "phases": [],
            }

        mock_memgraph.load_reference_dag.side_effect = load_dag

        alert = {
            "labels": {"alertname": "AmfDown", "nf": "amf"},
            "annotations": {},
            "startsAt": "2026-02-15T10:00:00Z",
        }

        with patch("triage_agent.agents.dag_mapper.get_memgraph", return_value=mock_memgraph):
            result = dag_mapper(self._make_state(alert))

        assert len(result["nf_union"]) == len(set(result["nf_union"]))
