"""Tests for FastAPI webhook endpoint."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from triage_agent.api.webhook import app


@pytest.fixture
def client() -> TestClient:
    """Create a FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def alertmanager_payload(sample_alert: dict[str, Any]) -> dict[str, Any]:
    """Full Alertmanager webhook payload."""
    return {
        "receiver": "triage-agent",
        "status": "firing",
        "alerts": [sample_alert],
        "groupLabels": {"alertname": "RegistrationFailures"},
        "commonLabels": {"namespace": "5g-core"},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "version": "4",
        "groupKey": "test-group",
    }


class TestRootEndpoint:
    """Tests for GET / endpoint."""

    def test_root_returns_api_info(self, client: TestClient) -> None:
        """Root endpoint should return API metadata."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "5G TriageAgent"
        assert data["version"] == "3.2.0"
        assert "docs" in data
        assert "health" in data
        assert "webhook" in data


class TestWebhookEndpoint:
    """Tests for POST /webhook endpoint."""

    def test_accepts_valid_payload(
        self, client: TestClient, alertmanager_payload: dict[str, Any]
    ) -> None:
        """Should accept valid Alertmanager payload and return 200."""
        response = client.post("/webhook", json=alertmanager_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["alerts_received"] == 1
        assert "incident_id" in data

    def test_generates_unique_incident_id(
        self, client: TestClient, alertmanager_payload: dict[str, Any]
    ) -> None:
        """Each request should generate a unique incident_id."""
        resp1 = client.post("/webhook", json=alertmanager_payload)
        resp2 = client.post("/webhook", json=alertmanager_payload)
        assert resp1.json()["incident_id"] != resp2.json()["incident_id"]

    def test_rejects_empty_alerts(self, client: TestClient) -> None:
        """Should return 400 when alerts list is empty."""
        payload = {
            "status": "firing",
            "alerts": [],
        }
        response = client.post("/webhook", json=payload)
        assert response.status_code == 400

    def test_skips_resolved_alerts(
        self, client: TestClient, sample_alert: dict[str, Any]
    ) -> None:
        """Should skip processing when all alerts are resolved."""
        resolved_alert = {**sample_alert, "status": "resolved"}
        payload = {
            "status": "resolved",
            "alerts": [resolved_alert],
        }
        response = client.post("/webhook", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"

    def test_counts_firing_alerts(
        self, client: TestClient, sample_alert: dict[str, Any]
    ) -> None:
        """Should report correct count of firing alerts."""
        firing = {**sample_alert, "status": "firing"}
        resolved = {**sample_alert, "status": "resolved"}
        payload = {
            "status": "firing",
            "alerts": [firing, resolved, firing],
        }
        response = client.post("/webhook", json=payload)
        data = response.json()
        assert data["alerts_received"] == 3
        assert "2 firing" in data["message"]

    def test_invalid_payload_returns_422(self, client: TestClient) -> None:
        """Should return 422 for malformed payload."""
        response = client.post("/webhook", json={"bad": "data"})
        assert response.status_code == 422


class TestAlertModels:
    """Tests for Pydantic alert models."""

    def test_alert_label_requires_alertname(self) -> None:
        """AlertLabel must have alertname."""
        from triage_agent.api.webhook import AlertLabel

        label = AlertLabel(alertname="TestAlert")
        assert label.alertname == "TestAlert"
        assert label.severity == "warning"
        assert label.namespace == "5g-core"

    def test_alert_model_required_fields(self) -> None:
        """Alert model requires status, labels, startsAt."""
        from triage_agent.api.webhook import Alert, AlertLabel

        alert = Alert(
            status="firing",
            labels=AlertLabel(alertname="Test"),
            startsAt="2026-02-15T10:00:00Z",
        )
        assert alert.status == "firing"
        assert alert.labels.alertname == "Test"

    def test_triage_response_model(self) -> None:
        """TriageResponse should have required fields."""
        from triage_agent.api.webhook import TriageResponse

        resp = TriageResponse(
            incident_id="test-001",
            status="accepted",
            message="Processing",
            alerts_received=1,
        )
        assert resp.incident_id == "test-001"
