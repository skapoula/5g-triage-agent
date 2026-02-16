#!/bin/bash
# scripts/load_dags.sh
# Load 3GPP reference DAGs into Memgraph

set -e

MEMGRAPH_HOST=${MEMGRAPH_HOST:-localhost}
MEMGRAPH_PORT=${MEMGRAPH_PORT:-7687}
DAG_DIR=${DAG_DIR:-./dags}
MAX_RETRIES=${MAX_RETRIES:-30}

echo "============================================"
echo "Memgraph DAG Loader"
echo "============================================"
echo "Host: $MEMGRAPH_HOST:$MEMGRAPH_PORT"
echo "DAG Directory: $DAG_DIR"
echo ""

# Wait for Memgraph to be ready
echo "Waiting for Memgraph to be ready..."
retry_count=0
until mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" <<< "RETURN 1;" > /dev/null 2>&1; do
    retry_count=$((retry_count + 1))
    if [ $retry_count -ge $MAX_RETRIES ]; then
        echo "ERROR: Memgraph not available after $MAX_RETRIES attempts"
        exit 1
    fi
    echo "  Attempt $retry_count/$MAX_RETRIES - waiting..."
    sleep 2
done
echo "✓ Memgraph is ready"
echo ""

# Check for .cypher files
if ! ls "$DAG_DIR"/*.cypher 1> /dev/null 2>&1; then
    echo "ERROR: No .cypher files found in $DAG_DIR"
    exit 1
fi

# Load each .cypher file
echo "Loading DAG definitions..."
for cypher_file in "$DAG_DIR"/*.cypher; do
    if [ -f "$cypher_file" ]; then
        filename=$(basename "$cypher_file")
        echo "  Loading: $filename"
        
        if mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" < "$cypher_file" > /dev/null 2>&1; then
            echo "    ✓ Loaded successfully"
        else
            echo "    ✗ Failed to load"
            exit 1
        fi
    fi
done
echo ""

# Verify DAGs loaded
echo "Verifying loaded DAGs..."
echo ""
mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" <<EOF
MATCH (t:ReferenceTrace)
OPTIONAL MATCH (t)-[:STEP]->(e:RefEvent)
RETURN t.name AS dag_name, t.spec AS spec, count(e) AS step_count
ORDER BY t.name;
EOF

# Count total
dag_count=$(mgconsole --host "$MEMGRAPH_HOST" --port "$MEMGRAPH_PORT" --output-format csv <<< "MATCH (t:ReferenceTrace) RETURN count(t) AS c;" | tail -1)
echo ""
echo "============================================"
echo "Total DAGs loaded: $dag_count"
echo "============================================"

if [ "$dag_count" -lt 1 ]; then
    echo "WARNING: No DAGs were loaded!"
    exit 1
fi

echo ""
echo "DAG loading complete!"
