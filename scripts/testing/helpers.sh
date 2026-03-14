#!/usr/bin/env bash
# helpers.sh — shared functions for all live test scripts
set -euo pipefail

# ── Environment ──────────────────────────────────────────────────────────────
export WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:8000}"
export TRIAGE_POD="${TRIAGE_POD:-}"
export RESULTS_DIR="${RESULTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/test-results/$(date +%Y%m%d-%H%M%S)}"
export PROMETHEUS_URL="http://kube-prom-kube-prometheus-prometheus.monitoring:9090"
export LOKI_URL="http://loki.monitoring:3100"
export UERANSIM_NS="5g-core"
export CORE_NS="5g-core"
export TRIAGE_NS="monitoring"

mkdir -p "$RESULTS_DIR"

# ── Logging ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date +%H:%M:%S)] $*"; }
pass() { echo "[PASS] $*" | tee -a "$RESULTS_DIR/summary.txt"; }
fail() { echo "[FAIL] $*" | tee -a "$RESULTS_DIR/summary.txt"; }
info() { echo "[INFO] $*"; }

# ── Resolve pod name ──────────────────────────────────────────────────────────
resolve_triage_pod() {
  TRIAGE_POD=$(kubectl get pod -n "$TRIAGE_NS" -l app=triage-agent \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "$TRIAGE_POD" ]]; then
    fail "triage-agent pod not found in namespace $TRIAGE_NS"
    exit 1
  fi
  export TRIAGE_POD
  log "Using pod: $TRIAGE_POD"
}

# ── Webhook trigger ───────────────────────────────────────────────────────────
# Usage: trigger_webhook <alertname> <nf> [severity=critical]
# Returns: incident_id
trigger_webhook() {
  local alertname=$1
  local nf=$2
  local severity=${3:-critical}
  local starts_at
  starts_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  curl -s -X POST "$WEBHOOK_URL/webhook" \
    -H "Content-Type: application/json" \
    -d "{
      \"receiver\": \"triage-agent\",
      \"status\": \"firing\",
      \"alerts\": [{
        \"status\": \"firing\",
        \"labels\": {
          \"alertname\": \"$alertname\",
          \"nf\": \"$nf\",
          \"namespace\": \"$CORE_NS\",
          \"severity\": \"$severity\"
        },
        \"startsAt\": \"$starts_at\"
      }]
    }" | jq -r '.incident_id'
}

# ── Poll incident until complete ──────────────────────────────────────────────
# Usage: poll_incident <incident_id> [timeout_seconds=360]
# Returns: final_report JSON (also saved to RESULTS_DIR)
poll_incident() {
  local incident_id=$1
  local timeout=${2:-360}
  local elapsed=0
  local result

  log "Polling incident $incident_id (timeout ${timeout}s)..."
  while [[ $elapsed -lt $timeout ]]; do
    result=$(curl -s "$WEBHOOK_URL/incidents/$incident_id")
    local status
    status=$(echo "$result" | jq -r '.status // "unknown"')

    if [[ "$status" == "complete" ]]; then
      echo "$result" | jq . | tee "$RESULTS_DIR/${incident_id}.json"
      log "Incident $incident_id complete"
      echo "$result"
      return 0
    fi

    log "Status: $status (${elapsed}s elapsed)"
    sleep 10
    elapsed=$((elapsed + 10))
  done

  fail "Incident $incident_id did not complete within ${timeout}s"
  return 1
}

# ── Per-IMSI Loki check ───────────────────────────────────────────────────────
# Usage: check_imsi_loki [lookback_minutes=5]
# Prints each IMSI with stream count; returns 1 if any IMSI has 0 streams
check_imsi_loki() {
  local lookback_min=${1:-5}
  local start end all_found=true

  start=$(date -d "${lookback_min} minutes ago" +%s)000000000
  end=$(date +%s)000000000

  for i in $(seq 1 10); do
    local imsi
    imsi=$(printf "imsi-20893000000000%d" "$i")
    local count
    count=$(curl -s \
      --data-urlencode "query={namespace=\"$CORE_NS\", pod=~\".*amf.*\"} |= \"$imsi\"" \
      --data-urlencode "start=$start" \
      --data-urlencode "end=$end" \
      --data-urlencode "limit=1" \
      "$LOKI_URL/loki/api/v1/query_range" \
      | jq '.data.result | length')
    echo "  $imsi: $count stream(s)"
    [[ "$count" -gt 0 ]] || all_found=false
  done

  $all_found && return 0 || return 1
}

# ── Token counter ─────────────────────────────────────────────────────────────
# Usage: collect_token_count <incident_id> <artifact_filename>
# Prints token count (1 token ≈ 4 chars)
collect_token_count() {
  local incident_id=$1
  local artifact=$2
  local content

  content=$(kubectl exec -n "$TRIAGE_NS" "$TRIAGE_POD" -c triage-agent -- \
    cat "/app/artifacts/$incident_id/$artifact" 2>/dev/null || echo "")

  if [[ -z "$content" ]]; then
    echo "  $artifact: NOT FOUND"
    return 1
  fi

  local chars token_est
  chars=${#content}
  token_est=$((chars / 4))
  echo "  $artifact: ~${token_est} tokens (${chars} chars)"
  echo "${incident_id}|${artifact}|${token_est}" >> "$RESULTS_DIR/token_counts.txt"
}

# ── Pull all artifacts for an incident ───────────────────────────────────────
pull_artifacts() {
  local incident_id=$1
  local dest="$RESULTS_DIR/artifacts/$incident_id"
  mkdir -p "$dest"
  kubectl cp "$TRIAGE_NS/$TRIAGE_POD:/app/artifacts/$incident_id" \
    "$dest" -c triage-agent 2>/dev/null || \
    log "Warning: could not pull artifacts for $incident_id"
  log "Artifacts saved to $dest"
}
