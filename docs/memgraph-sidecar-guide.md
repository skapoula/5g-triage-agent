# Memgraph Sidecar Guide
## Installation, Configuration, Testing, and Usage

This document covers the complete Memgraph integration for the 5G TriageAgent project.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Local Development Setup](#2-local-development-setup)
3. [Kubernetes Deployment](#3-kubernetes-deployment)
4. [DAG Schema and Loading](#4-dag-schema-and-loading)
5. [Python Integration](#5-python-integration)
6. [Testing Strategy](#6-testing-strategy)
7. [Operations and Debugging](#7-operations-and-debugging)

---

## 1. Architecture Overview

### Why Memgraph (Not Redis)

Memgraph is an in-memory graph database optimized for:

1. **Fast Cypher traversals** - Sub-100ms pattern matching on graphs with 20-150 nodes
2. **Streaming ingestion** - Real-time IMSI trace ingestion during incidents
3. **Subgraph isomorphism** - Compare captured traces against reference DAGs
4. **Minimal LLM surface area** - Deviation detection is deterministic, not LLM-based

### Sidecar Pattern

```
┌─────────────────────────────────────────────────────────────┐
│  Kubernetes Pod: triage-agent                               │
│                                                             │
│  ┌─────────────────┐         ┌─────────────────────────────┐│
│  │   memgraph      │  Bolt   │      triage-agent           ││
│  │   (sidecar)     │◄───────►│      (main container)       ││
│  │   :7687         │ Protocol│      :8000                  ││
│  └─────────────────┘         └─────────────────────────────┘│
│           ▲                                                  │
│           │                                                  │
│  ┌────────┴────────┐                                        │
│  │   dag-loader    │                                        │
│  │ (init container)│                                        │
│  └─────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

**Why sidecar, not separate service:**
- Latency: localhost communication vs network hop
- Availability: Pod-level lifecycle management
- Simplicity: No service discovery needed
- Isolation: Each replica has dedicated graph DB

---

## 2. Local Development Setup

### 2.1 Install Memgraph

**Option A: Docker (Recommended)**

```bash
# Start Memgraph container
docker run -d \
  --name memgraph-dev \
  -p 7687:7687 \
  -p 7444:7444 \
  -v memgraph-data:/var/lib/memgraph \
  memgraph/memgraph:latest \
  --storage-snapshot-interval-sec=60 \
  --storage-wal-enabled=true \
  --memory-limit=256

# Verify it's running
docker logs memgraph-dev

# Test connectivity
docker exec memgraph-dev mgconsole --host localhost --port 7687 <<< "RETURN 'Hello Memgraph!';"
```

**Option B: Native Installation (Ubuntu)**

```bash
# Add Memgraph repository
curl https://download.memgraph.com/memgraph-platform/v2.0.0/linux/memgraph-platform-2.0.0-1_amd64.deb -o memgraph.deb
sudo dpkg -i memgraph.deb

# Start service
sudo systemctl start memgraph
sudo systemctl enable memgraph

# Test
mgconsole --host localhost --port 7687 <<< "RETURN 1;"
```

### 2.2 Load Reference DAGs

```bash
# From project root
mgconsole --host localhost --port 7687 < dags/authentication_5g_aka.cypher
mgconsole --host localhost --port 7687 < dags/registration_general.cypher
mgconsole --host localhost --port 7687 < dags/pdu_session_establishment.cypher

# Verify DAGs loaded
mgconsole --host localhost --port 7687 <<< "MATCH (t:ReferenceTrace) RETURN t.name, t.spec;"
```

**Expected output:**
```
+----------------------------+-------------------------+
| t.name                     | t.spec                  |
+----------------------------+-------------------------+
| "Authentication_5G_AKA"    | "TS 33.501 6.1.3.2"    |
| "Registration_General"     | "TS 23.502 4.2.2.2.2"  |
| "PDU_Session_Establishment"| "TS 23.502 4.3.2.2.1"  |
+----------------------------+-------------------------+
```

### 2.3 Environment Configuration

```bash
# .env file for local development
MEMGRAPH_HOST=localhost
MEMGRAPH_PORT=7687

# Or export directly
export MEMGRAPH_HOST=localhost
export MEMGRAPH_PORT=7687
```

---

## 3. Kubernetes Deployment

### 3.1 ConfigMap for DAG Definitions

```yaml
# k8s/dag-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dag-definitions
  namespace: 5g-monitoring
data:
  authentication_5g_aka.cypher: |
    // Authentication_5G_AKA DAG (TS 33.501 Fig. 6.1.3.2-1)
    MATCH (t:ReferenceTrace {name: "Authentication_5G_AKA"}) DETACH DELETE t;
    
    CREATE (t:ReferenceTrace {
        name: "Authentication_5G_AKA",
        spec: "TS 33.501 6.1.3.2",
        version: "Rel-17",
        procedure: "authentication"
    });
    
    UNWIND [
        {order: 1, nf: "AMF", action: "Nausf_UEAuthentication_Authenticate Request", keywords: ["Nausf_UEAuthentication", "Authenticate", "SUPI"]},
        {order: 2, nf: "AUSF", action: "Nudm_UEAuthentication_Get Request", keywords: ["Nudm_UEAuthentication", "Get"]},
        {order: 3, nf: "UDM", action: "Authentication Vector Generation", keywords: ["AV", "Authentication Vector", "RAND", "AUTN"]},
        {order: 4, nf: "AUSF", action: "Nausf_UEAuthentication_Authenticate Response", keywords: ["5G-AIA", "Authentication Response"]},
        {order: 5, nf: "AMF", action: "Authentication Request to UE", keywords: ["Authentication Request", "RAND", "AUTN"]},
        {order: 6, nf: "UE", action: "Authentication Response", keywords: ["RES*", "Authentication Response"]},
        {order: 7, nf: "AMF", action: "Nausf_UEAuthentication_Authenticate (RES*)", keywords: ["RES*", "verification"]},
        {order: 8, nf: "AUSF", action: "RES* Verification", keywords: ["RES*", "XRES*", "verification", "success"]}
    ] AS step
    CREATE (e:RefEvent {
        order: step.order,
        nf: step.nf,
        action: step.action,
        keywords: step.keywords
    });
    
    MATCH (t:ReferenceTrace {name: "Authentication_5G_AKA"}), (e:RefEvent)
    WHERE e.order IS NOT NULL
    CREATE (t)-[:STEP {order: e.order}]->(e);
    
    MATCH (t:ReferenceTrace {name: "Authentication_5G_AKA"})-[:STEP]->(e1:RefEvent)
    MATCH (t)-[:STEP]->(e2:RefEvent)
    WHERE e2.order = e1.order + 1
    CREATE (e1)-[:NEXT]->(e2);

  registration_general.cypher: |
    // ... (content from dags/registration_general.cypher)

  pdu_session_establishment.cypher: |
    // ... (content from dags/pdu_session_establishment.cypher)
```

### 3.2 PersistentVolumeClaim

```yaml
# k8s/memgraph-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: memgraph-pvc
  namespace: 5g-monitoring
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: standard  # Adjust for your cluster
```

### 3.3 Complete Deployment

```yaml
# k8s/deployment-with-init.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: triage-agent
  namespace: 5g-monitoring
spec:
  replicas: 2
  selector:
    matchLabels:
      app: triage-agent
  template:
    metadata:
      labels:
        app: triage-agent
    spec:
      # Init container: preload DAGs into Memgraph
      initContainers:
      - name: dag-loader
        image: memgraph/memgraph:latest
        command: ["/bin/sh", "-c"]
        args:
        - |
          # Wait for Memgraph sidecar to be ready
          echo "Waiting for Memgraph to start..."
          until echo "RETURN 1;" | mgconsole --host localhost --port 7687 2>/dev/null; do
            sleep 2
          done
          
          echo "Memgraph ready. Loading DAGs..."
          
          # Load each Cypher DAG definition from ConfigMap
          for cypher_file in /dags/*.cypher; do
            echo "Loading: $cypher_file"
            mgconsole --host localhost --port 7687 < "$cypher_file"
            if [ $? -eq 0 ]; then
              echo "  ✓ Loaded successfully"
            else
              echo "  ✗ Failed to load"
              exit 1
            fi
          done
          
          # Verify DAGs loaded
          echo "Verifying DAGs..."
          dag_count=$(echo "MATCH (t:ReferenceTrace) RETURN count(t) AS c;" | \
            mgconsole --host localhost --port 7687 --output-format csv | tail -1)
          echo "Total DAGs loaded: $dag_count"
          
          if [ "$dag_count" -lt 3 ]; then
            echo "ERROR: Expected at least 3 DAGs, found $dag_count"
            exit 1
          fi
          
          echo "DAG loading complete!"
        volumeMounts:
        - name: dag-definitions
          mountPath: /dags
          readOnly: true
      
      containers:
      # Memgraph sidecar
      - name: memgraph
        image: memgraph/memgraph:2.14
        ports:
        - containerPort: 7687
          name: bolt
        args:
          - "--storage-snapshot-interval-sec=60"
          - "--storage-wal-enabled=true"
          - "--memory-limit=256"
          - "--log-level=WARNING"
        volumeMounts:
        - name: memgraph-data
          mountPath: /var/lib/memgraph
        resources:
          requests:
            cpu: 250m
            memory: 128Mi
          limits:
            cpu: 500m
            memory: 256Mi
        livenessProbe:
          exec:
            command:
              - sh
              - -c
              - "echo 'RETURN 1;' | mgconsole --host localhost --port 7687"
          initialDelaySeconds: 10
          periodSeconds: 10
          timeoutSeconds: 5
        readinessProbe:
          exec:
            command:
              - sh
              - -c
              - "echo 'MATCH (t:ReferenceTrace) RETURN count(t);' | mgconsole --host localhost --port 7687"
          initialDelaySeconds: 15
          periodSeconds: 10
      
      # Main application
      - name: triage-agent
        image: triage-agent:v3.2
        ports:
        - containerPort: 8000
          name: http
        env:
        - name: MEMGRAPH_HOST
          value: "localhost"  # Sidecar is on localhost
        - name: MEMGRAPH_PORT
          value: "7687"
        - name: LLM_API_KEY
          valueFrom:
            secretKeyRef:
              name: llm-credentials
              key: api-key
        resources:
          requests:
            cpu: 1
            memory: 2Gi
          limits:
            cpu: 2
            memory: 4Gi
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
      
      volumes:
      - name: dag-definitions
        configMap:
          name: dag-definitions
      - name: memgraph-data
        persistentVolumeClaim:
          claimName: memgraph-pvc
```

### 3.4 Apply to Cluster

```bash
# Create namespace
kubectl create namespace 5g-monitoring

# Apply resources
kubectl apply -f k8s/dag-configmap.yaml
kubectl apply -f k8s/memgraph-pvc.yaml
kubectl apply -f k8s/deployment-with-init.yaml

# Verify deployment
kubectl get pods -n 5g-monitoring -w

# Check init container logs
kubectl logs -n 5g-monitoring deployment/triage-agent -c dag-loader

# Check Memgraph logs
kubectl logs -n 5g-monitoring deployment/triage-agent -c memgraph

# Exec into pod to test Memgraph
kubectl exec -it -n 5g-monitoring deployment/triage-agent -c memgraph -- \
  mgconsole --host localhost --port 7687 <<< "MATCH (t:ReferenceTrace) RETURN t.name;"
```

---

## 4. DAG Schema and Loading

### 4.1 Graph Schema

```
(:ReferenceTrace)
    - name: string (unique identifier)
    - spec: string (3GPP reference)
    - version: string (e.g., "Rel-17")
    - procedure: string (e.g., "registration", "authentication")
    
    -[:STEP {order: int}]-> (:RefEvent)

(:RefEvent)
    - order: int (sequence number)
    - nf: string (network function)
    - action: string (step description)
    - keywords: list[string] (for log matching)
    - optional: boolean
    - sub_dag: string (reference to sub-procedure)
    
    -[:NEXT]-> (:RefEvent)  # Sequential flow
    -[:USES_SUB_DAG]-> (:ReferenceTrace)  # Sub-procedure link

(:CapturedTrace)
    - incident_id: string
    - imsi: string
    - created_at: timestamp
    
    -[:EVENT]-> (:TraceEvent)

(:TraceEvent)
    - order: int
    - action: string
    - timestamp: int
    - nf: string
    - raw_log: string
```

### 4.2 DAG Loading Script

```bash
#!/bin/bash
# scripts/load_dags.sh

set -e

MEMGRAPH_HOST=${MEMGRAPH_HOST:-localhost}
MEMGRAPH_PORT=${MEMGRAPH_PORT:-7687}
DAG_DIR=${DAG_DIR:-./dags}

echo "Loading DAGs into Memgraph at $MEMGRAPH_HOST:$MEMGRAPH_PORT"

# Wait for Memgraph
echo "Waiting for Memgraph..."
until mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" <<< "RETURN 1;" 2>/dev/null; do
    sleep 1
done
echo "Memgraph is ready"

# Load each .cypher file
for cypher_file in "$DAG_DIR"/*.cypher; do
    if [ -f "$cypher_file" ]; then
        echo "Loading: $cypher_file"
        mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" < "$cypher_file"
        echo "  ✓ Done"
    fi
done

# Verify
echo ""
echo "Verification:"
mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" <<EOF
MATCH (t:ReferenceTrace)
OPTIONAL MATCH (t)-[:STEP]->(e:RefEvent)
RETURN t.name AS dag, t.spec AS spec, count(e) AS steps
ORDER BY t.name;
EOF

echo ""
echo "DAG loading complete!"
```

---

## 5. Python Integration

### 5.1 Connection Module

See `src/triage_agent/memgraph/connection.py` for the full implementation. Key methods:

```python
from triage_agent.memgraph import get_memgraph

# Get singleton connection
mg = get_memgraph()

# Health check
if mg.health_check():
    print("Memgraph is healthy")

# Load a reference DAG
dag = mg.load_reference_dag("Registration_General")
print(f"DAG has {len(dag['phases'])} phases")
print(f"NFs involved: {dag['all_nfs']}")

# Ingest a captured trace
events = [
    {"order": 1, "action": "Registration Request", "timestamp": 1708000000, "nf": "UE"},
    {"order": 2, "action": "AMF selection", "timestamp": 1708000001, "nf": "AMF"},
    # ...
]
mg.ingest_captured_trace(
    incident_id="INC-001",
    imsi="001010123456789",
    events=events,
)

# Detect deviations
deviation = mg.detect_deviation(
    incident_id="INC-001",
    imsi="001010123456789",
    dag_name="Registration_General",
)
if deviation:
    print(f"Deviation at step {deviation['deviation_point']}")
    print(f"Expected: {deviation['expected']}")
    print(f"Actual: {deviation['actual']}")

# Cleanup after investigation
mg.cleanup_incident_traces("INC-001")
```

### 5.2 Usage in UeTracesAgent

```python
# src/triage_agent/agents/ue_traces_agent.py

from triage_agent.memgraph import get_memgraph

async def discover_and_trace_imsis(state: TriageState) -> TriageState:
    """UeTracesAgent: IMSI discovery, trace construction, deviation detection."""
    
    mg = get_memgraph()
    incident_id = state["incident_id"]
    dag_name = state.get("procedure_name", "Registration_General")
    
    # 1. Discover IMSIs from logs (via Loki)
    imsis = await discover_imsis_from_logs(state)
    
    # 2. For each IMSI, construct and ingest trace
    deviations = []
    for imsi in imsis:
        # Build trace events from logs
        events = await build_trace_events(imsi, state)
        
        # Ingest into Memgraph
        mg.ingest_captured_trace(
            incident_id=incident_id,
            imsi=imsi,
            events=events,
        )
        
        # Detect deviation against reference DAG
        deviation = mg.detect_deviation(
            incident_id=incident_id,
            imsi=imsi,
            dag_name=dag_name,
        )
        
        if deviation:
            deviations.append({
                "imsi": imsi,
                **deviation,
            })
    
    # Update state
    state["discovered_imsis"] = imsis
    state["traces_ready"] = True
    state["trace_deviations"] = deviations
    
    return state
```

---

## 6. Testing Strategy

### 6.1 Unit Tests (Mocked Memgraph)

```python
# tests/unit/test_memgraph_connection.py

import pytest
from unittest.mock import MagicMock, patch

from triage_agent.memgraph.connection import MemgraphConnection


class TestMemgraphConnection:
    """Unit tests for Memgraph connection with mocked driver."""

    def test_health_check_success(self) -> None:
        """Test health check returns True when Memgraph is healthy."""
        with patch("triage_agent.memgraph.connection.GraphDatabase") as mock_gd:
            mock_session = MagicMock()
            mock_session.run.return_value = [{"health": 1}]
            mock_gd.driver.return_value.session.return_value.__enter__ = MagicMock(
                return_value=mock_session
            )
            mock_gd.driver.return_value.session.return_value.__exit__ = MagicMock()

            conn = MemgraphConnection("bolt://localhost:7687")
            assert conn.health_check() is True

    def test_health_check_failure(self) -> None:
        """Test health check returns False when Memgraph is unavailable."""
        with patch("triage_agent.memgraph.connection.GraphDatabase") as mock_gd:
            mock_gd.driver.return_value.session.side_effect = Exception("Connection refused")

            conn = MemgraphConnection("bolt://localhost:7687")
            assert conn.health_check() is False

    def test_load_reference_dag(self) -> None:
        """Test loading a reference DAG returns correct structure."""
        mock_result = [
            {
                "name": "Registration_General",
                "spec": "TS 23.502",
                "procedure": "registration",
                "phases": [
                    {"order": 1, "nf": "UE", "action": "Registration Request"},
                    {"order": 2, "nf": "AMF", "action": "AMF selection"},
                ],
            }
        ]

        with patch("triage_agent.memgraph.connection.GraphDatabase") as mock_gd:
            mock_session = MagicMock()
            mock_session.run.return_value = [MagicMock(**{
                "__iter__": lambda self: iter([mock_result[0]]),
                "keys.return_value": list(mock_result[0].keys()),
            })]
            mock_gd.driver.return_value.session.return_value.__enter__ = MagicMock(
                return_value=mock_session
            )

            conn = MemgraphConnection("bolt://localhost:7687")
            # This will need adjustment based on actual implementation
            # dag = conn.load_reference_dag("Registration_General")
            # assert dag["name"] == "Registration_General"

    def test_detect_deviation_found(self) -> None:
        """Test deviation detection returns deviation details."""
        # Similar mocking pattern for deviation detection
        pass

    def test_detect_deviation_not_found(self) -> None:
        """Test deviation detection returns None when trace matches DAG."""
        pass
```

### 6.2 Integration Tests (Real Memgraph)

```python
# tests/integration/test_memgraph_integration.py

import pytest
from triage_agent.memgraph.connection import MemgraphConnection


@pytest.fixture(scope="module")
def memgraph_connection(request):
    """Create Memgraph connection for integration tests."""
    url = request.config.getoption("--memgraph-url", default="bolt://localhost:7687")
    conn = MemgraphConnection(url)
    
    # Verify connection
    if not conn.health_check():
        pytest.skip("Memgraph not available")
    
    yield conn
    conn.close()


@pytest.fixture
def clean_test_data(memgraph_connection):
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


class TestMemgraphIntegration:
    """Integration tests requiring real Memgraph instance."""

    def test_health_check(self, memgraph_connection: MemgraphConnection) -> None:
        """Verify Memgraph health check works."""
        assert memgraph_connection.health_check() is True

    def test_reference_dags_loaded(self, memgraph_connection: MemgraphConnection) -> None:
        """Verify reference DAGs are loaded."""
        result = memgraph_connection.execute_cypher(
            "MATCH (t:ReferenceTrace) RETURN t.name AS name ORDER BY name"
        )
        
        dag_names = [r["name"] for r in result]
        assert "Authentication_5G_AKA" in dag_names
        assert "Registration_General" in dag_names

    def test_load_registration_dag(self, memgraph_connection: MemgraphConnection) -> None:
        """Test loading Registration_General DAG."""
        dag = memgraph_connection.load_reference_dag("Registration_General")
        
        assert dag is not None
        assert dag["name"] == "Registration_General"
        assert "AMF" in dag["all_nfs"]
        assert len(dag["phases"]) > 0

    def test_ingest_and_detect_deviation(
        self, 
        memgraph_connection: MemgraphConnection,
        clean_test_data,
    ) -> None:
        """Test full flow: ingest trace, detect deviation."""
        incident_id = "test-incident-001"
        imsi = "001010123456789"
        
        # Ingest a trace with a deviation at step 9
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
        
        assert deviation is not None
        assert deviation["deviation_point"] == 9
        assert "Authentication" in deviation["expected"]
        assert "FAILED" in deviation["actual"]

    def test_cleanup_incident_traces(
        self,
        memgraph_connection: MemgraphConnection,
        clean_test_data,
    ) -> None:
        """Test cleanup removes all traces for an incident."""
        incident_id = "test-cleanup-001"
        
        # Create some traces
        for i, imsi in enumerate(["imsi1", "imsi2", "imsi3"]):
            memgraph_connection.ingest_captured_trace(
                incident_id=incident_id,
                imsi=imsi,
                events=[{"order": 1, "action": "test", "timestamp": 1708000000, "nf": "UE"}],
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
```

### 6.3 pytest Configuration

```python
# conftest.py additions

def pytest_addoption(parser):
    parser.addoption(
        "--memgraph-url",
        action="store",
        default="bolt://localhost:7687",
        help="Memgraph Bolt URL for integration tests",
    )
```

### 6.4 Running Tests

```bash
# Unit tests only (no Memgraph needed)
pytest tests/unit/ -v

# Integration tests (requires Memgraph)
# First start Memgraph:
docker run -d --name memgraph-test -p 7687:7687 memgraph/memgraph

# Load DAGs:
./scripts/load_dags.sh

# Run integration tests:
pytest tests/integration/ -v --memgraph-url bolt://localhost:7687

# All tests with coverage:
pytest tests/ --cov=triage_agent --cov-report=html
```

---

## 7. Operations and Debugging

### 7.1 mgconsole Commands

```bash
# Connect to Memgraph
mgconsole --host localhost --port 7687

# Show all node labels
SHOW NODE LABELS;

# Show all relationship types
SHOW RELATIONSHIP TYPES;

# Count nodes by type
MATCH (n) RETURN labels(n) AS type, count(n) AS count;

# List all reference DAGs
MATCH (t:ReferenceTrace) RETURN t.name, t.spec;

# View a specific DAG's steps
MATCH (t:ReferenceTrace {name: "Registration_General"})-[:STEP]->(e:RefEvent)
RETURN e.order, e.nf, e.action
ORDER BY e.order;

# View captured traces for an incident
MATCH (t:CapturedTrace {incident_id: "INC-001"})-[:EVENT]->(e:TraceEvent)
RETURN t.imsi, e.order, e.action, e.timestamp
ORDER BY t.imsi, e.order;

# Find all deviations for an incident
MATCH (ref:ReferenceTrace {name: "Registration_General"})-[:STEP]->(r:RefEvent)
MATCH (trace:CapturedTrace {incident_id: "INC-001"})-[:EVENT]->(e:TraceEvent)
WHERE r.order = e.order AND NOT e.action CONTAINS r.action
RETURN trace.imsi, r.order AS step, r.action AS expected, e.action AS actual;

# Memory usage
SHOW STORAGE INFO;

# Trigger snapshot
SNAPSHOT;
```

### 7.2 Kubernetes Debugging

```bash
# Check pod status
kubectl get pods -n 5g-monitoring -l app=triage-agent

# View init container logs (DAG loading)
kubectl logs -n 5g-monitoring deployment/triage-agent -c dag-loader

# View Memgraph logs
kubectl logs -n 5g-monitoring deployment/triage-agent -c memgraph

# Exec into Memgraph container
kubectl exec -it -n 5g-monitoring deployment/triage-agent -c memgraph -- bash

# Run mgconsole inside pod
kubectl exec -it -n 5g-monitoring deployment/triage-agent -c memgraph -- \
  mgconsole --host localhost --port 7687

# Port-forward for local access
kubectl port-forward -n 5g-monitoring deployment/triage-agent 7687:7687
# Then locally: mgconsole --host localhost --port 7687
```

### 7.3 Common Issues

| Issue | Symptom | Solution |
|-------|---------|----------|
| Connection refused | `ServiceUnavailable` error | Check Memgraph is running, verify port 7687 |
| DAGs not loaded | Empty results from `MATCH (t:ReferenceTrace)` | Check init container logs, re-run load script |
| Memory exhaustion | Memgraph crashes, OOM in logs | Increase `--memory-limit`, check for trace cleanup |
| Slow queries | Queries >100ms | Add indexes, check query patterns |
| Stale data | Old traces not cleaned | Run `cleanup_incident_traces()` or manual DELETE |

### 7.4 Adding Indexes (Performance)

```cypher
-- Index for fast incident lookup
CREATE INDEX ON :CapturedTrace(incident_id);

-- Index for IMSI lookup
CREATE INDEX ON :CapturedTrace(imsi);

-- Index for DAG name lookup
CREATE INDEX ON :ReferenceTrace(name);

-- Verify indexes
SHOW INDEX INFO;
```

---

## Summary

This guide covers:

1. **Architecture**: Why Memgraph, why sidecar pattern
2. **Local Setup**: Docker or native installation, DAG loading
3. **Kubernetes**: ConfigMap, PVC, init container, deployment
4. **Python Integration**: Connection class, usage patterns
5. **Testing**: Unit tests with mocks, integration tests with real Memgraph
6. **Operations**: mgconsole commands, k8s debugging, common issues

For questions about the 3GPP DAG structure, refer to `.claude/agents/5g-protocol-reviewer.md`.
For Cypher query optimization, refer to `.claude/agents/memgraph-expert.md`.
