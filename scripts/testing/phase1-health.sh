#!/usr/bin/env bash
# phase1-health.sh — verify TriageAgent pod health, DAGs, and endpoints
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"
resolve_triage_pod

log "=== Phase 1: Health Verification ==="
ERRORS=0

# 1. Container states
log "Checking container states..."
kubectl get pods -n "$TRIAGE_NS" | tee "$RESULTS_DIR/phase1-pods.txt"

DAG_LOADER_STATUS=$(kubectl get pod -n "$TRIAGE_NS" "$TRIAGE_POD" \
  -o jsonpath='{.status.initContainerStatuses[?(@.name=="dag-loader")].state.terminated.reason}' \
  2>/dev/null || echo "")
MEMGRAPH_READY=$(kubectl get pod -n "$TRIAGE_NS" "$TRIAGE_POD" \
  -o jsonpath='{.status.containerStatuses[?(@.name=="memgraph")].ready}' 2>/dev/null || echo "false")
TRIAGE_READY=$(kubectl get pod -n "$TRIAGE_NS" "$TRIAGE_POD" \
  -o jsonpath='{.status.containerStatuses[?(@.name=="triage-agent")].ready}' 2>/dev/null || echo "false")

[[ "$DAG_LOADER_STATUS" == "Completed" ]] && \
  pass "dag-loader: Completed" || { fail "dag-loader: $DAG_LOADER_STATUS"; ERRORS=$((ERRORS+1)); }
[[ "$MEMGRAPH_READY" == "true" ]] && \
  pass "memgraph: Ready" || { fail "memgraph: not ready"; ERRORS=$((ERRORS+1)); }
[[ "$TRIAGE_READY" == "true" ]] && \
  pass "triage-agent: Ready" || { fail "triage-agent: not ready"; ERRORS=$((ERRORS+1)); }

# 2. DAG names loaded (exact PascalCase required)
log "Verifying DAGs in Memgraph..."
DAG_OUTPUT=$(kubectl exec -n "$TRIAGE_NS" "$TRIAGE_POD" -c memgraph -- \
  bash -c 'echo "MATCH (t:ReferenceTrace) RETURN t.name;" | mgconsole' 2>/dev/null \
  | tee "$RESULTS_DIR/phase1-dags.txt")

for DAG_NAME in "Registration_General" "Authentication_5G_AKA" "PDU_Session_Establishment"; do
  if echo "$DAG_OUTPUT" | grep -q "$DAG_NAME"; then
    pass "DAG loaded: $DAG_NAME"
  else
    fail "DAG missing: $DAG_NAME"
    ERRORS=$((ERRORS+1))
  fi
done

# 3. /health endpoint — all dependencies green
log "Checking /health endpoint..."
HEALTH=$(curl -s "$WEBHOOK_URL/health" | tee "$RESULTS_DIR/phase1-health.json")
echo "$HEALTH" | jq .
HEALTH_STATUS=$(echo "$HEALTH" | jq -r '.status')
MEMGRAPH_OK=$(echo "$HEALTH" | jq -r '.memgraph')
PROMETHEUS_OK=$(echo "$HEALTH" | jq -r '.prometheus')
LOKI_OK=$(echo "$HEALTH" | jq -r '.loki')

[[ "$HEALTH_STATUS" == "healthy" && "$MEMGRAPH_OK" == "true" \
  && "$PROMETHEUS_OK" == "true" && "$LOKI_OK" == "true" ]] && \
  pass "/health: healthy, memgraph=true, prometheus=true, loki=true" || \
  { fail "/health check failed: $HEALTH"; ERRORS=$((ERRORS+1)); }

# 4. /health/ready
log "Checking /health/ready..."
READY_CODE=$(curl -o /dev/null -s -w "%{http_code}" "$WEBHOOK_URL/health/ready")
[[ "$READY_CODE" == "200" ]] && \
  pass "/health/ready: 200 OK" || { fail "/health/ready: $READY_CODE"; ERRORS=$((ERRORS+1)); }

# Summary
echo ""
if [[ "$ERRORS" -eq 0 ]]; then
  pass "Phase 1 PASSED — TriageAgent healthy and ready"
  exit 0
else
  fail "Phase 1 FAILED — $ERRORS check(s) failed"
  exit 1
fi
