"""Tests for FastAPI webhook endpoint."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from triage_agent.api.webhook import app


@pytest.fixture(autouse=True)
def _reset_incident_store() -> None:
    """Clear the in-memory incident store before every unit test."""
    from triage_agent.api.webhook import _incident_store

    _incident_store.clear()


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


class TestHealthEndpoint:
    """Tests for GET /health endpoint."""

    def test_health_returns_status_healthy(self, client: TestClient) -> None:
        """Health check should return healthy when all services are up."""
        mock_memgraph = MagicMock()
        mock_memgraph.health_check.return_value = True

        mock_mcp = AsyncMock()
        mock_mcp.health_check_prometheus.return_value = True
        mock_mcp.health_check_loki.return_value = True
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("triage_agent.memgraph.connection.get_memgraph", return_value=mock_memgraph),
            patch("triage_agent.mcp.client.MCPClient", return_value=mock_mcp),
        ):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["memgraph"] is True
        assert data["prometheus"] is True
        assert data["loki"] is True
        assert "timestamp" in data

    def test_health_returns_degraded_when_memgraph_down(
        self, client: TestClient
    ) -> None:
        """Health check should return degraded when Memgraph is unavailable."""
        mock_memgraph = MagicMock()
        mock_memgraph.health_check.return_value = False

        mock_mcp = AsyncMock()
        mock_mcp.health_check_prometheus.return_value = True
        mock_mcp.health_check_loki.return_value = True
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("triage_agent.memgraph.connection.get_memgraph", return_value=mock_memgraph),
            patch("triage_agent.mcp.client.MCPClient", return_value=mock_mcp),
        ):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["memgraph"] is False

    def test_health_returns_degraded_when_prometheus_down(
        self, client: TestClient
    ) -> None:
        """Health check should return degraded when Prometheus is unavailable."""
        mock_memgraph = MagicMock()
        mock_memgraph.health_check.return_value = True

        mock_mcp = AsyncMock()
        mock_mcp.health_check_prometheus.return_value = False
        mock_mcp.health_check_loki.return_value = True
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("triage_agent.memgraph.connection.get_memgraph", return_value=mock_memgraph),
            patch("triage_agent.mcp.client.MCPClient", return_value=mock_mcp),
        ):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["prometheus"] is False


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
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            mock_triage.return_value = None
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
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            mock_triage.return_value = None
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
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            mock_triage.return_value = None
            response = client.post("/webhook", json=payload)
        data = response.json()
        assert data["alerts_received"] == 3
        assert "2 firing" in data["message"]

    def test_invalid_payload_returns_422(self, client: TestClient) -> None:
        """Should return 422 for malformed payload."""
        response = client.post("/webhook", json={"bad": "data"})
        assert response.status_code == 422

    def test_schedules_background_task_for_firing_alert(
        self, client: TestClient, alertmanager_payload: dict[str, Any]
    ) -> None:
        """Firing alert should schedule _run_triage as a background task."""
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            mock_triage.return_value = None
            response = client.post("/webhook", json=alertmanager_payload)
        assert response.status_code == 200
        mock_triage.assert_called_once()

    def test_background_task_receives_alert_dict_and_incident_id(
        self, client: TestClient, alertmanager_payload: dict[str, Any]
    ) -> None:
        """Background task should receive the first firing alert dict and incident_id."""
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            mock_triage.return_value = None
            response = client.post("/webhook", json=alertmanager_payload)
        assert response.status_code == 200
        args, kwargs = mock_triage.call_args
        # First positional arg is the alert dict
        alert_dict = args[0]
        incident_id = args[1]
        assert isinstance(alert_dict, dict)
        assert alert_dict["status"] == "firing"
        assert isinstance(incident_id, str)

    def test_response_returned_without_waiting_for_workflow(
        self, client: TestClient, alertmanager_payload: dict[str, Any]
    ) -> None:
        """Webhook should return accepted status immediately; workflow runs in background."""
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            mock_triage.return_value = None
            response = client.post("/webhook", json=alertmanager_payload)
        data = response.json()
        assert data["status"] == "accepted"
        assert "incident_id" in data

    def test_background_task_not_scheduled_for_resolved_alerts(
        self, client: TestClient, sample_alert: dict[str, Any]
    ) -> None:
        """No background task should be scheduled when all alerts are resolved."""
        resolved_alert = {**sample_alert, "status": "resolved"}
        payload = {"status": "resolved", "alerts": [resolved_alert]}
        with patch("triage_agent.api.webhook._run_triage") as mock_triage:
            response = client.post("/webhook", json=payload)
        assert response.json()["status"] == "skipped"
        mock_triage.assert_not_called()


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


def test_health_check_degraded_when_loki_down() -> None:
    """Health check returns degraded when Loki is unavailable."""
    mock_memgraph = MagicMock()
    mock_memgraph.health_check.return_value = True

    mock_mcp = AsyncMock()
    mock_mcp.health_check_prometheus = AsyncMock(return_value=True)
    mock_mcp.health_check_loki = AsyncMock(return_value=False)
    mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
    mock_mcp.__aexit__ = AsyncMock(return_value=None)

    with patch("triage_agent.memgraph.connection.get_memgraph", return_value=mock_memgraph), \
         patch("triage_agent.mcp.client.MCPClient", return_value=mock_mcp):
        client = TestClient(app)
        resp = client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["loki"] is False
    assert data["status"] == "degraded"


def test_health_check_healthy_when_all_ok() -> None:
    """Health check returns healthy when all services are up."""
    mock_memgraph = MagicMock()
    mock_memgraph.health_check.return_value = True

    mock_mcp = AsyncMock()
    mock_mcp.health_check_prometheus = AsyncMock(return_value=True)
    mock_mcp.health_check_loki = AsyncMock(return_value=True)
    mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
    mock_mcp.__aexit__ = AsyncMock(return_value=None)

    with patch("triage_agent.memgraph.connection.get_memgraph", return_value=mock_memgraph), \
         patch("triage_agent.mcp.client.MCPClient", return_value=mock_mcp):
        client = TestClient(app)
        resp = client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"


class TestIncidentRoute:
    """Unit tests for GET /incidents/{incident_id}."""

    def test_unknown_incident_returns_404(self, client: TestClient) -> None:
        resp = client.get("/incidents/no-such-id")
        assert resp.status_code == 404

    def test_pending_incident_returns_pending(self, client: TestClient) -> None:
        """Store entry written as None (pending) before background task fires."""
        with patch("triage_agent.api.webhook._run_triage"):
            resp = client.post(
                "/webhook",
                json={
                    "status": "firing",
                    "alerts": [
                        {
                            "status": "firing",
                            "labels": {
                                "alertname": "TestAlert",
                                "severity": "critical",
                                "namespace": "5g-core",
                                "nf": "AMF",
                            },
                            "annotations": {"summary": "t", "description": "t"},
                            "startsAt": "2026-03-08T10:00:00Z",
                        }
                    ],
                },
            )
        incident_id = resp.json()["incident_id"]
        poll = client.get(f"/incidents/{incident_id}")
        assert poll.status_code == 200
        assert poll.json()["status"] == "pending"
        assert poll.json()["incident_id"] == incident_id

    def test_complete_incident_returns_report(self, client: TestClient) -> None:
        """Manually seed a complete report and verify the endpoint returns it."""
        from triage_agent.api.webhook import _incident_store

        _incident_store["manual-001"] = {
            "incident_id": "manual-001",
            "layer": "application",
            "root_nf": "AMF",
            "confidence": 0.85,
        }
        poll = client.get("/incidents/manual-001")
        assert poll.status_code == 200
        data = poll.json()
        assert data["status"] == "complete"
        assert data["final_report"]["layer"] == "application"

    def test_failed_incident_returns_failed_status(self, client: TestClient) -> None:
        from triage_agent.api.webhook import _incident_store

        _incident_store["failed-001"] = {"error": "triage_failed"}
        poll = client.get("/incidents/failed-001")
        assert poll.status_code == 200
        assert poll.json()["status"] == "failed"
        assert poll.json()["final_report"]["error"] == "triage_failed"
