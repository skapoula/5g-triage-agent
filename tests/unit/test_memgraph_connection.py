"""Tests for MemgraphConnection — test-first, DO NOT implement yet.

All tests mock the neo4j driver to avoid requiring a live Memgraph instance.
Tests are written against the public API defined in
src/triage_agent/memgraph/connection.py.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from neo4j.exceptions import ServiceUnavailable, TransientError

from triage_agent.memgraph.connection import MemgraphConnection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRecord:
    """Minimal stand-in for neo4j.Record that supports ``dict()`` conversion."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __iter__(self) -> Iterator[tuple[str, object]]:
        return iter(self._data.items())

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def get(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_driver() -> MagicMock:
    """Create a mock neo4j driver with session context-manager wiring."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver


@pytest.fixture
def conn(mock_driver: MagicMock) -> MemgraphConnection:
    """MemgraphConnection wired to the mock driver."""
    with patch(
        "triage_agent.memgraph.connection.GraphDatabase"
    ) as mock_gd:
        mock_gd.driver.return_value = mock_driver
        connection = MemgraphConnection("bolt://localhost:7687")
    return connection


def _session(mock_driver: MagicMock) -> MagicMock:
    """Convenience accessor for the mock session."""
    result: MagicMock = mock_driver.session.return_value.__enter__.return_value
    return result


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """MemgraphConnection.health_check"""

    def test_returns_true_when_connected(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """health_check returns True when Memgraph responds with 1."""
        session = _session(mock_driver)
        session.run.return_value = [_FakeRecord({"health": 1})]

        assert conn.health_check() is True

    def test_returns_false_on_connection_error(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """health_check returns False when the driver raises."""
        session = _session(mock_driver)
        session.run.side_effect = ServiceUnavailable("Connection refused")

        assert conn.health_check() is False

    def test_returns_false_on_unexpected_result(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """health_check returns False when query returns unexpected data."""
        session = _session(mock_driver)
        session.run.return_value = []  # empty result

        assert conn.health_check() is False


# ---------------------------------------------------------------------------
# execute_cypher
# ---------------------------------------------------------------------------


class TestExecuteCypher:
    """MemgraphConnection.execute_cypher"""

    def test_returns_list_of_dicts(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """execute_cypher converts neo4j Records to plain dicts."""
        session = _session(mock_driver)
        session.run.return_value = [
            _FakeRecord({"name": "AMF", "count": 5}),
            _FakeRecord({"name": "SMF", "count": 3}),
        ]

        result = conn.execute_cypher("MATCH (n) RETURN n.name AS name, count(n) AS count")

        assert result == [
            {"name": "AMF", "count": 5},
            {"name": "SMF", "count": 3},
        ]

    def test_passes_params_to_session_run(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """execute_cypher forwards parameters to session.run."""
        session = _session(mock_driver)
        session.run.return_value = []

        conn.execute_cypher("MATCH (n {id: $id}) RETURN n", {"id": "abc"})

        session.run.assert_called_once_with(
            "MATCH (n {id: $id}) RETURN n", {"id": "abc"}
        )

    def test_empty_params_default(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """execute_cypher sends empty dict when params is None."""
        session = _session(mock_driver)
        session.run.return_value = []

        conn.execute_cypher("RETURN 1")

        session.run.assert_called_once_with("RETURN 1", {})

    @patch("triage_agent.memgraph.connection.time.sleep", return_value=None)
    def test_retries_on_transient_error(
        self,
        mock_sleep: MagicMock,
        conn: MemgraphConnection,
        mock_driver: MagicMock,
    ) -> None:
        """execute_cypher retries up to max_retries on TransientError."""
        session = _session(mock_driver)
        session.run.side_effect = [
            TransientError("deadlock"),
            TransientError("deadlock"),
            [_FakeRecord({"ok": True})],
        ]

        result = conn.execute_cypher("RETURN 1 AS ok", max_retries=3)

        assert result == [{"ok": True}]
        assert session.run.call_count == 3
        # Exponential backoff: sleep(2**0), sleep(2**1)
        mock_sleep.assert_has_calls([call(1), call(2)])

    @patch("triage_agent.memgraph.connection.time.sleep", return_value=None)
    def test_raises_after_max_retries_exhausted(
        self,
        mock_sleep: MagicMock,
        conn: MemgraphConnection,
        mock_driver: MagicMock,
    ) -> None:
        """execute_cypher raises TransientError after all retries fail."""
        session = _session(mock_driver)
        session.run.side_effect = TransientError("deadlock")

        with pytest.raises(TransientError):
            conn.execute_cypher("RETURN 1", max_retries=3)

        assert session.run.call_count == 3

    @patch("triage_agent.memgraph.connection.time.sleep", return_value=None)
    def test_retries_on_service_unavailable(
        self,
        mock_sleep: MagicMock,
        conn: MemgraphConnection,
        mock_driver: MagicMock,
    ) -> None:
        """execute_cypher retries on ServiceUnavailable."""
        session = _session(mock_driver)
        session.run.side_effect = [
            ServiceUnavailable("disconnected"),
            [_FakeRecord({"val": 42})],
        ]

        result = conn.execute_cypher("RETURN 42 AS val", max_retries=2)

        assert result == [{"val": 42}]
        assert session.run.call_count == 2


# ---------------------------------------------------------------------------
# load_reference_dag
# ---------------------------------------------------------------------------


class TestLoadReferenceDag:
    """MemgraphConnection.load_reference_dag"""

    def test_returns_correct_structure(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """load_reference_dag returns dict with name, spec, procedure, phases, all_nfs."""
        session = _session(mock_driver)
        session.run.return_value = [
            _FakeRecord({
                "name": "Registration_General",
                "spec": "TS 23.502 4.2.2.2.2",
                "procedure": "registration",
                "phases": [
                    {"order": 2, "nf": "AMF", "action": "AMF selection",
                     "keywords": ["AMF"], "optional": None, "sub_dag": None},
                    {"order": 1, "nf": "UE", "action": "Registration Request",
                     "keywords": ["Registration"], "optional": False, "sub_dag": None},
                    {"order": 3, "nf": "AUSF", "action": "Auth",
                     "keywords": ["Auth"], "optional": False, "sub_dag": "Authentication_5G_AKA"},
                ],
            }),
        ]

        dag = conn.load_reference_dag("Registration_General")

        assert dag is not None
        assert dag["name"] == "Registration_General"
        assert dag["spec"] == "TS 23.502 4.2.2.2.2"
        assert dag["procedure"] == "registration"

        # Phases must be sorted by order
        orders = [p["order"] for p in dag["phases"]]
        assert orders == [1, 2, 3]

        # all_nfs extracted from phases
        assert set(dag["all_nfs"]) == {"UE", "AMF", "AUSF"}

    def test_returns_none_for_missing_dag(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """load_reference_dag returns None when DAG does not exist."""
        session = _session(mock_driver)
        session.run.return_value = []

        result = conn.load_reference_dag("NonExistent_DAG")

        assert result is None

    def test_phases_contain_expected_fields(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """Each phase dict should contain order, nf, action, keywords."""
        session = _session(mock_driver)
        session.run.return_value = [
            _FakeRecord({
                "name": "Authentication_5G_AKA",
                "spec": "TS 33.501 6.1.3.2",
                "procedure": "authentication",
                "phases": [
                    {"order": 1, "nf": "AMF", "action": "Nausf_UEAuthentication_Authenticate Request",
                     "keywords": ["Nausf_UEAuthentication"], "optional": None, "sub_dag": None},
                ],
            }),
        ]

        dag = conn.load_reference_dag("Authentication_5G_AKA")

        assert dag is not None
        phase = dag["phases"][0]
        assert "order" in phase
        assert "nf" in phase
        assert "action" in phase
        assert "keywords" in phase


# ---------------------------------------------------------------------------
# ingest_captured_trace
# ---------------------------------------------------------------------------


class TestIngestCapturedTrace:
    """MemgraphConnection.ingest_captured_trace"""

    def test_creates_trace_and_events(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """ingest_captured_trace calls execute_cypher_write with correct params."""
        session = _session(mock_driver)
        session.run.return_value = MagicMock()

        events: list[dict[str, Any]] = [
            {"order": 1, "action": "Registration Request", "timestamp": 1708000000, "nf": "UE"},
            {"order": 2, "action": "AMF selection", "timestamp": 1708000001, "nf": "AMF"},
        ]

        conn.ingest_captured_trace(
            incident_id="INC-001",
            imsi="001010123456789",
            events=events,
        )

        session.run.assert_called_once()
        call_args = session.run.call_args
        query: str = call_args[0][0]
        params: dict[str, Any] = call_args[0][1]

        # Query should create CapturedTrace and TraceEvent nodes
        assert "CapturedTrace" in query
        assert "TraceEvent" in query
        assert ":EVENT" in query

        # Params should contain the supplied data
        assert params["incident_id"] == "INC-001"
        assert params["imsi"] == "001010123456789"
        assert params["events"] == events

    def test_handles_empty_events_list(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """ingest_captured_trace does not raise when events is empty."""
        session = _session(mock_driver)
        session.run.return_value = MagicMock()

        # Should not raise
        conn.ingest_captured_trace(
            incident_id="INC-002",
            imsi="001010000000000",
            events=[],
        )

        session.run.assert_called_once()


# ---------------------------------------------------------------------------
# detect_deviation
# ---------------------------------------------------------------------------


class TestDetectDeviation:
    """MemgraphConnection.detect_deviation"""

    def test_returns_deviation_dict(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """detect_deviation returns first deviation as a dict."""
        session = _session(mock_driver)
        session.run.return_value = [
            _FakeRecord({
                "deviation_point": 9,
                "expected": "Authentication/Security",
                "actual": "Authentication FAILED",
                "expected_nf": "AMF",
                "actual_nf": "AMF",
            }),
        ]

        result = conn.detect_deviation(
            incident_id="INC-001",
            imsi="001010123456789",
            dag_name="Registration_General",
        )

        assert result is not None
        assert result["deviation_point"] == 9
        assert result["expected"] == "Authentication/Security"
        assert result["actual"] == "Authentication FAILED"
        assert result["expected_nf"] == "AMF"
        assert result["actual_nf"] == "AMF"

    def test_returns_none_when_no_deviation(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """detect_deviation returns None when trace matches reference DAG."""
        session = _session(mock_driver)
        session.run.return_value = []

        result = conn.detect_deviation(
            incident_id="INC-001",
            imsi="001010123456789",
            dag_name="Registration_General",
        )

        assert result is None

    def test_passes_all_params_to_query(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """detect_deviation forwards incident_id, imsi, and dag_name as params."""
        session = _session(mock_driver)
        session.run.return_value = []

        conn.detect_deviation(
            incident_id="INC-042",
            imsi="001019999999999",
            dag_name="Authentication_5G_AKA",
        )

        session.run.assert_called_once()
        params: dict[str, str] = session.run.call_args[0][1]
        assert params["incident_id"] == "INC-042"
        assert params["imsi"] == "001019999999999"
        assert params["dag_name"] == "Authentication_5G_AKA"


# ---------------------------------------------------------------------------
# cleanup_incident_traces
# ---------------------------------------------------------------------------


class TestCleanupIncidentTraces:
    """MemgraphConnection.cleanup_incident_traces"""

    def test_removes_captured_traces(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """cleanup_incident_traces issues DETACH DELETE for the incident."""
        session = _session(mock_driver)
        session.run.return_value = MagicMock()

        conn.cleanup_incident_traces("INC-001")

        session.run.assert_called_once()
        query: str = session.run.call_args[0][0]
        params: dict[str, str] = session.run.call_args[0][1]

        assert "DETACH DELETE" in query
        assert "CapturedTrace" in query
        assert params["incident_id"] == "INC-001"

    def test_targets_only_specified_incident(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """cleanup_incident_traces scopes deletion to the given incident_id."""
        session = _session(mock_driver)
        session.run.return_value = MagicMock()

        conn.cleanup_incident_traces("INC-SPECIFIC")

        params: dict[str, str] = session.run.call_args[0][1]
        assert params["incident_id"] == "INC-SPECIFIC"


# ---------------------------------------------------------------------------
# close / context manager
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    """MemgraphConnection.close and context-manager protocol."""

    def test_close_closes_driver(
        self, conn: MemgraphConnection, mock_driver: MagicMock
    ) -> None:
        """close() delegates to the underlying driver.close()."""
        conn.close()
        mock_driver.close.assert_called_once()

    def test_context_manager(self, mock_driver: MagicMock) -> None:
        """MemgraphConnection can be used as a context manager."""
        with patch(
            "triage_agent.memgraph.connection.GraphDatabase"
        ) as mock_gd:
            mock_gd.driver.return_value = mock_driver

            with MemgraphConnection("bolt://localhost:7687") as mg:
                assert mg is not None

            mock_driver.close.assert_called_once()
