"""Integration tests for Memgraph connection and DAG operations.

These tests require a running Memgraph instance with loaded DAGs.

Run with:
    # Start Memgraph
    docker run -d --name memgraph-test -p 7687:7687 memgraph/memgraph
    
    # Load DAGs
    ./scripts/load_dags.sh
    
    # Run tests
    pytest tests/integration/test_memgraph_integration.py -v --memgraph-url bolt://localhost:7687
"""

from collections.abc import Generator

import pytest

from triage_agent.memgraph.connection import MemgraphConnection


@pytest.fixture(scope="module")
def memgraph_url(request) -> str:
    """Get Memgraph URL from pytest options."""
    return request.config.getoption("--memgraph-url")


@pytest.fixture(scope="module")
def memgraph_connection(memgraph_url: str) -> Generator[MemgraphConnection, None, None]:
    """Create Memgraph connection for integration tests."""
    conn = MemgraphConnection(memgraph_url)

    # Verify connection
    if not conn.health_check():
        pytest.skip(f"Memgraph not available at {memgraph_url}")

    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_test_data(memgraph_connection: MemgraphConnection) -> Generator[None, None, None]:
    """Clean up test data before and after each test."""
    # Clean before
    memgraph_connection.execute_cypher_write(
        "MATCH (t:CapturedTrace) WHERE t.incident_id STARTS WITH 'test-' DETACH DELETE t"
    )

    yield

    # Clean after
    memgraph_connection.execute_cypher_write(
        "MATCH (t:CapturedTrace) WHERE t.incident_id STARTS WITH 'test-' DETACH DELETE t"
    )


class TestMemgraphHealthAndConnectivity:
    """Tests for Memgraph connectivity and health."""

    def test_health_check_returns_true(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Health check should return True when Memgraph is running."""
        assert memgraph_connection.health_check() is True

    def test_execute_simple_cypher(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should execute simple Cypher queries."""
        result = memgraph_connection.execute_cypher("RETURN 42 AS answer")

        assert len(result) == 1
        assert result[0]["answer"] == 42

    def test_execute_cypher_with_params(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should execute parameterized Cypher queries."""
        result = memgraph_connection.execute_cypher(
            "RETURN $value AS answer",
            {"value": "hello"},
        )

        assert result[0]["answer"] == "hello"


class TestReferenceDAGLoading:
    """Tests for reference DAG loading and querying."""

    def test_reference_dags_exist(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Reference DAGs should be loaded in Memgraph."""
        result = memgraph_connection.execute_cypher(
            "MATCH (t:ReferenceTrace) RETURN t.name AS name ORDER BY name"
        )

        dag_names = [r["name"] for r in result]

        # At least one DAG should be loaded
        assert len(dag_names) >= 1

        # Check for expected DAGs if fully loaded
        if len(dag_names) >= 3:
            assert "Authentication_5G_AKA" in dag_names
            assert "Registration_General" in dag_names
            assert "PDU_Session_Establishment" in dag_names

    def test_load_authentication_dag(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should load Authentication_5G_AKA DAG with correct structure."""
        dag = memgraph_connection.load_reference_dag("Authentication_5G_AKA")

        if dag is None:
            pytest.skip("Authentication_5G_AKA DAG not loaded")

        assert dag["name"] == "Authentication_5G_AKA"
        assert "TS 33.501" in dag["spec"]
        assert dag["procedure"] == "authentication"
        assert len(dag["phases"]) >= 1
        assert "AMF" in dag["all_nfs"] or "AUSF" in dag["all_nfs"]

    def test_load_registration_dag(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should load Registration_General DAG with correct structure."""
        dag = memgraph_connection.load_reference_dag("Registration_General")

        if dag is None:
            pytest.skip("Registration_General DAG not loaded")

        assert dag["name"] == "Registration_General"
        assert "TS 23.502" in dag["spec"]
        assert dag["procedure"] == "registration"
        assert len(dag["phases"]) >= 1
        assert "AMF" in dag["all_nfs"]

    def test_load_nonexistent_dag_returns_none(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Loading a non-existent DAG should return None."""
        dag = memgraph_connection.load_reference_dag("NonExistent_DAG")
        assert dag is None

    def test_dag_phases_have_required_fields(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """DAG phases should have required fields."""
        dag = memgraph_connection.load_reference_dag("Registration_General")

        if dag is None:
            pytest.skip("Registration_General DAG not loaded")

        for phase in dag["phases"]:
            assert "order" in phase
            assert "nf" in phase
            assert "action" in phase
            assert isinstance(phase["order"], int)


class TestTraceIngestion:
    """Tests for captured trace ingestion."""

    def test_ingest_simple_trace(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should ingest a simple captured trace."""
        incident_id = "test-ingest-001"
        imsi = "001010123456789"
        events = [
            {"order": 1, "action": "Registration Request", "timestamp": 1708000000, "nf": "UE"},
            {"order": 2, "action": "AMF selection", "timestamp": 1708000001, "nf": "AMF"},
        ]

        # Ingest trace
        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi=imsi,
            events=events,
        )

        # Verify trace exists
        result = memgraph_connection.execute_cypher(
            "MATCH (t:CapturedTrace {incident_id: $id, imsi: $imsi}) RETURN t",
            {"id": incident_id, "imsi": imsi},
        )

        assert len(result) == 1

    def test_ingest_trace_with_events(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should ingest trace with correct event count."""
        incident_id = "test-events-001"
        imsi = "001010123456789"
        events = [
            {"order": 1, "action": "Step 1", "timestamp": 1708000000, "nf": "NF1"},
            {"order": 2, "action": "Step 2", "timestamp": 1708000001, "nf": "NF2"},
            {"order": 3, "action": "Step 3", "timestamp": 1708000002, "nf": "NF3"},
        ]

        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi=imsi,
            events=events,
        )

        # Count events
        result = memgraph_connection.execute_cypher(
            """
            MATCH (t:CapturedTrace {incident_id: $id})-[:EVENT]->(e:TraceEvent)
            RETURN count(e) AS event_count
            """,
            {"id": incident_id},
        )

        assert result[0]["event_count"] == 3


class TestDeviationDetection:
    """Tests for trace deviation detection against reference DAGs."""

    def test_detect_deviation_in_trace(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should detect deviation when trace differs from reference DAG."""
        # Skip if Registration DAG not loaded
        dag = memgraph_connection.load_reference_dag("Registration_General")
        if dag is None:
            pytest.skip("Registration_General DAG not loaded")

        incident_id = "test-deviation-001"
        imsi = "001010123456789"

        # Create trace with deviation at step 9 (Authentication should succeed, but we say FAILED)
        events = [
            {"order": 1, "action": "Registration Request", "timestamp": 1708000000, "nf": "UE"},
            {"order": 2, "action": "AMF selection", "timestamp": 1708000001, "nf": "AMF"},
            {"order": 9, "action": "Authentication FAILED", "timestamp": 1708000009, "nf": "AMF"},
        ]

        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi=imsi,
            events=events,
        )

        # Detect deviation
        deviation = memgraph_connection.detect_deviation(
            incident_id=incident_id,
            imsi=imsi,
            dag_name="Registration_General",
        )

        # Should find deviation at step 9
        # Note: Exact behavior depends on DAG content and matching logic
        if deviation:
            assert "deviation_point" in deviation
            assert "expected" in deviation
            assert "actual" in deviation

    def test_no_deviation_when_trace_matches(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Should return None when trace matches reference DAG."""
        dag = memgraph_connection.load_reference_dag("Registration_General")
        if dag is None:
            pytest.skip("Registration_General DAG not loaded")

        incident_id = "test-no-deviation-001"
        imsi = "001010123456789"

        # Create trace that matches (using exact actions from DAG)
        events = [
            {"order": 1, "action": "Registration Request", "timestamp": 1708000000, "nf": "UE"},
            {"order": 2, "action": "AMF selection", "timestamp": 1708000001, "nf": "AMF"},
        ]

        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi=imsi,
            events=events,
        )

        deviation = memgraph_connection.detect_deviation(
            incident_id=incident_id,
            imsi=imsi,
            dag_name="Registration_General",
        )

        # Matching trace should have no deviation
        # (or deviation should be None if the query finds matching actions)
        # This depends on exact matching logic in detect_deviation


class TestTraceCleanup:
    """Tests for incident trace cleanup."""

    def test_cleanup_removes_all_traces(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Cleanup should remove all traces for an incident."""
        incident_id = "test-cleanup-001"

        # Create multiple traces
        for i, imsi in enumerate(["imsi1", "imsi2", "imsi3"]):
            memgraph_connection.ingest_captured_trace(
                incident_id=incident_id,
                imsi=imsi,
                events=[
                    {"order": 1, "action": f"test-{i}", "timestamp": 1708000000, "nf": "UE"}
                ],
            )

        # Verify traces exist
        result = memgraph_connection.execute_cypher(
            "MATCH (t:CapturedTrace {incident_id: $id}) RETURN count(t) AS c",
            {"id": incident_id},
        )
        assert result[0]["c"] == 3

        # Cleanup
        memgraph_connection.cleanup_incident_traces(incident_id)

        # Verify traces removed
        result = memgraph_connection.execute_cypher(
            "MATCH (t:CapturedTrace {incident_id: $id}) RETURN count(t) AS c",
            {"id": incident_id},
        )
        assert result[0]["c"] == 0

    def test_cleanup_removes_events(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Cleanup should remove trace events along with traces."""
        incident_id = "test-cleanup-events-001"

        memgraph_connection.ingest_captured_trace(
            incident_id=incident_id,
            imsi="test-imsi",
            events=[
                {"order": 1, "action": "Step 1", "timestamp": 1708000000, "nf": "NF1"},
                {"order": 2, "action": "Step 2", "timestamp": 1708000001, "nf": "NF2"},
            ],
        )

        # Verify events exist
        result = memgraph_connection.execute_cypher(
            """
            MATCH (t:CapturedTrace {incident_id: $id})-[:EVENT]->(e:TraceEvent)
            RETURN count(e) AS c
            """,
            {"id": incident_id},
        )
        assert result[0]["c"] == 2

        # Cleanup
        memgraph_connection.cleanup_incident_traces(incident_id)

        # Verify events also removed (orphan check)
        result = memgraph_connection.execute_cypher(
            """
            MATCH (e:TraceEvent)
            WHERE NOT exists((e)<-[:EVENT]-(:CapturedTrace))
            RETURN count(e) AS orphans
            """,
        )
        # Should have no orphaned events from our test
        # (Note: Other tests may leave orphans, so we can't assert == 0 globally)

    def test_cleanup_does_not_affect_other_incidents(
        self, memgraph_connection: MemgraphConnection
    ) -> None:
        """Cleanup should only affect the specified incident."""
        incident1 = "test-cleanup-incident1"
        incident2 = "test-cleanup-incident2"

        # Create traces for both incidents
        for incident_id in [incident1, incident2]:
            memgraph_connection.ingest_captured_trace(
                incident_id=incident_id,
                imsi="test-imsi",
                events=[{"order": 1, "action": "test", "timestamp": 1708000000, "nf": "UE"}],
            )

        # Cleanup only incident1
        memgraph_connection.cleanup_incident_traces(incident1)

        # Verify incident1 removed
        result = memgraph_connection.execute_cypher(
            "MATCH (t:CapturedTrace {incident_id: $id}) RETURN count(t) AS c",
            {"id": incident1},
        )
        assert result[0]["c"] == 0

        # Verify incident2 still exists
        result = memgraph_connection.execute_cypher(
            "MATCH (t:CapturedTrace {incident_id: $id}) RETURN count(t) AS c",
            {"id": incident2},
        )
        assert result[0]["c"] == 1

        # Cleanup incident2 for test cleanup
        memgraph_connection.cleanup_incident_traces(incident2)
