"""End-to-end tests for the 5G TriageAgent webhook API.

Run against a live server:
    pytest tests/e2e/ -v --alert-webhook http://localhost:8000

Run in-process (no stack required — workflow is mocked):
    pytest tests/e2e/ -v

In-process mode exercises the full FastAPI request/response cycle including
routing, Pydantic validation, and background-task dispatch, but replaces the
LangGraph workflow with a deterministic stub so no external services are needed.
"""
from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Alertmanager payload builders
# ---------------------------------------------------------------------------

STARTS_AT = "2026-03-06T10:00:00Z"


def _alertmanager_payload(
    alertname: str,
    nf: str,
    severity: str = "critical",
    status: str = "firing",
    summary: str = "",
    description: str = "",
) -> dict[str, Any]:
    return {
        "receiver": "triage-agent",
        "status": status,
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": alertname,
                    "severity": severity,
                    "namespace": "5g-core",
                    "nf": nf,
                },
                "annotations": {
                    "summary": summary or f"{alertname} detected on {nf}",
                    "description": description or f"{nf} {alertname} exceeded threshold",
                },
                "startsAt": STARTS_AT,
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "abc123def456",
            }
        ],
        "groupLabels": {"alertname": alertname},
        "commonLabels": {"severity": severity},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "version": "4",
        "groupKey": f"{{alertname={alertname}}}",
    }


def _registration_payload() -> dict[str, Any]:
    return _alertmanager_payload(
        alertname="RegistrationFailureRate",
        nf="AMF",
        summary="High registration failure rate",
        description="AMF registration failure rate > 10%",
    )


def _pdu_session_payload() -> dict[str, Any]:
    return _alertmanager_payload(
        alertname="PDUSessionFailureRate",
        nf="SMF",
        summary="High PDU session failure rate",
        description="SMF PDU session failure rate > 5%",
    )


def _auth_failure_payload() -> dict[str, Any]:
    return _alertmanager_payload(
        alertname="AuthenticationFailureRate",
        nf="AUSF",
        severity="warning",
        summary="Elevated authentication failures",
        description="AUSF 5G AKA failure rate elevated",
    )


def _resolved_payload() -> dict[str, Any]:
    """All alerts resolved — should be skipped (no firing alerts)."""
    payload = _registration_payload()
    payload["status"] = "resolved"
    payload["alerts"][0]["status"] = "resolved"
    return payload


def _empty_alerts_payload() -> dict[str, Any]:
    payload = _registration_payload()
    payload["alerts"] = []
    return payload


def _mock_final_report(incident_id: str, layer: str, root_nf: str) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "layer": layer,
        "root_nf": root_nf,
        "failure_mode": "test_failure",
        "confidence": 0.85,
        "evidence_quality_score": 0.80,
        "infra_score": 0.15,
        "procedure_name": "Registration_General",
        "attempt_count": 1,
        "evidence_chain": [],
    }


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def http_client(server_base_url: str | None) -> Generator[httpx.Client, None, None]:
    """Return an httpx.Client pointed at either the live server or TestClient."""
    if server_base_url is not None:
        # Live server — no mocking, real stack required.
        with httpx.Client(base_url=server_base_url, timeout=30.0) as client:
            yield client
    else:
        # In-process — mock the LangGraph workflow so no external services needed.
        from triage_agent.api.webhook import app

        stub_state: dict[str, Any] = {}

        def _fake_invoke(state: dict[str, Any]) -> dict[str, Any]:
            incident_id = state.get("incident_id", "stub-incident")
            state["final_report"] = _mock_final_report(
                incident_id=incident_id,
                layer="application",
                root_nf="AMF",
            )
            stub_state.update(state)
            return state

        mock_workflow = MagicMock()
        mock_workflow.invoke.side_effect = _fake_invoke

        with patch("triage_agent.api.webhook._workflow", mock_workflow):
            with TestClient(app, raise_server_exceptions=True) as tc:
                # Wrap TestClient in an httpx.Client-compatible shim.
                # TestClient IS an httpx.Client so we can yield it directly.
                yield tc  # type: ignore[misc]


@pytest.fixture(scope="session")
def is_live(server_base_url: str | None) -> bool:
    return server_base_url is not None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200(self, http_client: httpx.Client) -> None:
        resp = http_client.get("/health")
        assert resp.status_code == 200

    def test_health_response_schema(self, http_client: httpx.Client) -> None:
        data = http_client.get("/health").json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")
        assert "timestamp" in data
        assert "memgraph" in data
        assert "prometheus" in data
        assert "loki" in data
        assert isinstance(data["memgraph"], bool)
        assert isinstance(data["prometheus"], bool)
        assert isinstance(data["loki"], bool)

    def test_health_timestamp_is_utc_iso(self, http_client: httpx.Client) -> None:
        data = http_client.get("/health").json()
        ts = data["timestamp"]
        assert ts.endswith("Z"), f"Expected UTC ISO timestamp ending in Z, got: {ts}"


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------


class TestRootEndpoint:
    def test_root_returns_200(self, http_client: httpx.Client) -> None:
        resp = http_client.get("/")
        assert resp.status_code == 200

    def test_root_response_schema(self, http_client: httpx.Client) -> None:
        data = http_client.get("/").json()
        assert data["name"] == "5G TriageAgent"
        assert "version" in data
        assert data["webhook"] == "/webhook"
        assert data["health"] == "/health"


# ---------------------------------------------------------------------------
# Webhook — accepted scenarios
# ---------------------------------------------------------------------------


class TestWebhookAcceptedAlerts:
    def test_registration_failure_accepted(self, http_client: httpx.Client) -> None:
        resp = http_client.post("/webhook", json=_registration_payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["alerts_received"] == 1
        assert "incident_id" in data
        assert len(data["incident_id"]) > 0

    def test_pdu_session_failure_accepted(self, http_client: httpx.Client) -> None:
        resp = http_client.post("/webhook", json=_pdu_session_payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["alerts_received"] == 1

    def test_auth_failure_accepted(self, http_client: httpx.Client) -> None:
        resp = http_client.post("/webhook", json=_auth_failure_payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"

    def test_accepted_response_has_unique_incident_ids(
        self, http_client: httpx.Client
    ) -> None:
        ids = {
            http_client.post("/webhook", json=_registration_payload()).json()["incident_id"]
            for _ in range(3)
        }
        assert len(ids) == 3, "Each request must produce a unique incident_id"

    def test_accepted_message_mentions_firing_count(
        self, http_client: httpx.Client
    ) -> None:
        data = http_client.post("/webhook", json=_registration_payload()).json()
        assert "1" in data["message"] or "firing" in data["message"].lower()


# ---------------------------------------------------------------------------
# Webhook — skipped scenarios
# ---------------------------------------------------------------------------


class TestWebhookSkippedAlerts:
    def test_resolved_alert_is_skipped(self, http_client: httpx.Client) -> None:
        resp = http_client.post("/webhook", json=_resolved_payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"
        assert data["alerts_received"] == 1

    def test_skipped_response_has_incident_id(self, http_client: httpx.Client) -> None:
        data = http_client.post("/webhook", json=_resolved_payload()).json()
        assert "incident_id" in data
        assert len(data["incident_id"]) > 0


# ---------------------------------------------------------------------------
# Webhook — rejected / error scenarios
# ---------------------------------------------------------------------------


class TestWebhookRejectedAlerts:
    def test_empty_alerts_returns_400(self, http_client: httpx.Client) -> None:
        resp = http_client.post("/webhook", json=_empty_alerts_payload())
        assert resp.status_code == 400

    def test_invalid_json_returns_422(self, http_client: httpx.Client) -> None:
        resp = http_client.post(
            "/webhook",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_missing_required_field_returns_422(self, http_client: httpx.Client) -> None:
        # 'status' is required at the payload level
        payload = _registration_payload()
        del payload["status"]
        resp = http_client.post("/webhook", json=payload)
        assert resp.status_code == 422

    def test_missing_alertname_returns_422(self, http_client: httpx.Client) -> None:
        payload = _registration_payload()
        del payload["alerts"][0]["labels"]["alertname"]
        resp = http_client.post("/webhook", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Live-server-only: background workflow completion
# ---------------------------------------------------------------------------


class TestLiveWorkflowCompletion:
    """Polls server logs indirectly by checking that /health stays up after
    a triage run.  Only meaningful against a real running server."""

    def test_server_stays_healthy_after_triage(
        self, http_client: httpx.Client, is_live: bool
    ) -> None:
        if not is_live:
            pytest.skip("Live server not configured (--alert-webhook not set)")

        resp = http_client.post("/webhook", json=_registration_payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

        # Allow time for background triage to run
        time.sleep(5)

        health = http_client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] in ("healthy", "degraded")

    def test_multiple_concurrent_alerts_all_accepted(
        self, http_client: httpx.Client, is_live: bool
    ) -> None:
        if not is_live:
            pytest.skip("Live server not configured (--alert-webhook not set)")

        payloads = [
            _registration_payload(),
            _pdu_session_payload(),
            _auth_failure_payload(),
        ]
        results = [http_client.post("/webhook", json=p) for p in payloads]

        for resp in results:
            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"

        # All incident IDs must be distinct
        ids = {r.json()["incident_id"] for r in results}
        assert len(ids) == 3


# ---------------------------------------------------------------------------
# /incidents/{incident_id} — schema assertions (in-process and live)
# ---------------------------------------------------------------------------


class TestIncidentEndpoint:
    def test_unknown_incident_returns_404(self, http_client: httpx.Client) -> None:
        resp = http_client.get("/incidents/does-not-exist-00000000")
        assert resp.status_code == 404

    def test_accepted_incident_appears_in_store(self, http_client: httpx.Client) -> None:
        resp = http_client.post("/webhook", json=_registration_payload())
        assert resp.status_code == 200
        incident_id = resp.json()["incident_id"]

        poll = http_client.get(f"/incidents/{incident_id}")
        assert poll.status_code == 200
        data = poll.json()
        assert data["incident_id"] == incident_id
        assert data["status"] in ("pending", "complete", "failed")

    def test_incident_response_schema_after_completion(
        self, http_client: httpx.Client
    ) -> None:
        resp = http_client.post("/webhook", json=_registration_payload())
        incident_id = resp.json()["incident_id"]

        deadline = time.time() + 10
        poll_data: dict[str, Any] = {}
        while time.time() < deadline:
            r = http_client.get(f"/incidents/{incident_id}")
            poll_data = r.json()
            if poll_data["status"] != "pending":
                break
            time.sleep(0.5)

        assert poll_data["status"] in ("complete", "failed")
        assert "incident_id" in poll_data
        if poll_data["status"] == "complete":
            assert isinstance(poll_data["final_report"], dict)


# ---------------------------------------------------------------------------
# Live-server-only: full pipeline poll-to-completion
# ---------------------------------------------------------------------------


class TestLiveTriageCompletion:
    """Fire a real Alertmanager webhook at the live stack and poll
    /incidents/{id} until the RCA final_report is returned.

    Exercises the complete pipeline: InfraAgent → NfMetricsAgent +
    NfLogsAgent + UeTracesAgent → EvidenceQuality → RCAAgent — all
    hitting real Prometheus, Loki, and Memgraph in the 5g-core namespace.

    Only runs when --alert-webhook is supplied.
    """

    _POLL_INTERVAL_S = 3
    _TIMEOUT_S = 120

    def _poll_until_complete(
        self, http_client: httpx.Client, incident_id: str
    ) -> dict[str, Any]:
        deadline = time.time() + self._TIMEOUT_S
        while time.time() < deadline:
            r = http_client.get(f"/incidents/{incident_id}")
            assert r.status_code == 200
            data = r.json()
            if data["status"] in ("complete", "failed"):
                return data
            time.sleep(self._POLL_INTERVAL_S)
        raise TimeoutError(
            f"Triage did not complete within {self._TIMEOUT_S}s "
            f"for incident {incident_id}"
        )

    def test_registration_failure_produces_final_report(
        self, http_client: httpx.Client, is_live: bool
    ) -> None:
        if not is_live:
            pytest.skip("Live server not configured (--alert-webhook not set)")

        resp = http_client.post("/webhook", json=_registration_payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"
        incident_id = resp.json()["incident_id"]

        result = self._poll_until_complete(http_client, incident_id)

        assert result["status"] == "complete", f"Triage failed: {result}"
        report = result["final_report"]
        assert isinstance(report, dict)
        assert report["incident_id"] == incident_id
        assert report["layer"] in ("infrastructure", "application")
        assert isinstance(report["confidence"], float)
        assert 0.0 <= report["confidence"] <= 1.0
        assert isinstance(report["evidence_quality_score"], float)
        assert isinstance(report["evidence_chain"], list)
        assert report["attempt_count"] >= 1

    def test_pdu_session_failure_produces_final_report(
        self, http_client: httpx.Client, is_live: bool
    ) -> None:
        if not is_live:
            pytest.skip("Live server not configured (--alert-webhook not set)")

        resp = http_client.post("/webhook", json=_pdu_session_payload())
        assert resp.status_code == 200
        incident_id = resp.json()["incident_id"]

        result = self._poll_until_complete(http_client, incident_id)

        assert result["status"] == "complete", f"Triage failed: {result}"
        report = result["final_report"]
        assert report["incident_id"] == incident_id
        assert report["layer"] in ("infrastructure", "application")
        assert 0.0 <= report["confidence"] <= 1.0

    def test_auth_failure_produces_final_report(
        self, http_client: httpx.Client, is_live: bool
    ) -> None:
        if not is_live:
            pytest.skip("Live server not configured (--alert-webhook not set)")

        resp = http_client.post("/webhook", json=_auth_failure_payload())
        assert resp.status_code == 200
        incident_id = resp.json()["incident_id"]

        result = self._poll_until_complete(http_client, incident_id)

        assert result["status"] == "complete", f"Triage failed: {result}"
        report = result["final_report"]
        assert report["incident_id"] == incident_id
        assert 0.0 <= report["confidence"] <= 1.0
