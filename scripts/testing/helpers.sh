#!/usr/bin/env bash
# helpers.sh — shared functions for all live test scripts (local-pod variant)
set -euo pipefail

# ── Environment ──────────────────────────────────────────────────────────────
export WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:8000}"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-/workspace/net-rca/artifacts}"
export RESULTS_DIR="${RESULTS_DIR:-/workspace/net-rca/test-results/$(date +%Y%m%d-%H%M%S)}"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://kube-prom-kube-prometheus-prometheus.monitoring:9090}"
export LOKI_URL="${LOKI_URL:-http://loki.monitoring:3100}"
export MEMGRAPH_HOST="${MEMGRAPH_HOST:-localhost}"
export MEMGRAPH_PORT="${MEMGRAPH_PORT:-7687}"
export CORE_NS="5g-core"
export TRIAGE_LOG="${TRIAGE_LOG:-/tmp/triage-agent.log}"

mkdir -p "$RESULTS_DIR"

# ── Logging ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date +%H:%M:%S)] $*"; }
pass() { echo "[PASS] $*" | tee -a "$RESULTS_DIR/summary.txt"; }
fail() { echo "[FAIL] $*" | tee -a "$RESULTS_DIR/summary.txt"; }
info() { echo "[INFO] $*"; }

# ── Verify local TriageAgent is running ───────────────────────────────────────
check_local_agent() {
  if ! curl -s --max-time 3 "$WEBHOOK_URL/health" > /dev/null 2>&1; then
    fail "TriageAgent not reachable at $WEBHOOK_URL — run phase05-start-local.sh first"
    exit 1
  fi
  log "TriageAgent reachable at $WEBHOOK_URL"
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
      --data-urlencode "query={k8s_namespace_name=\"$CORE_NS\", k8s_pod_name=~\".*amf.*\"} |= \"$imsi\"" \
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

# ── Token counter (local filesystem) ─────────────────────────────────────────
# Usage: collect_token_count <incident_id> <artifact_filename>
collect_token_count() {
  local incident_id=$1
  local artifact=$2
  local artifact_path="$ARTIFACTS_DIR/$incident_id/$artifact"

  if [[ ! -f "$artifact_path" ]]; then
    echo "  $artifact: NOT FOUND (checked $artifact_path)"
    return 1
  fi

  local chars token_est
  chars=$(wc -c < "$artifact_path")
  token_est=$((chars / 4))
  echo "  $artifact: ~${token_est} tokens (${chars} chars)"
  echo "${incident_id}|${artifact}|${token_est}" >> "$RESULTS_DIR/token_counts.txt"
}

# ── Copy local artifacts to results dir ──────────────────────────────────────
pull_artifacts() {
  local incident_id=$1
  local src="$ARTIFACTS_DIR/$incident_id"
  local dest="$RESULTS_DIR/artifacts/$incident_id"

  mkdir -p "$dest"
  if [[ -d "$src" ]]; then
    cp -r "$src/." "$dest/"
    log "Artifacts copied: $src → $dest"
  else
    log "Warning: no artifact directory found at $src"
  fi
}

# ── Memgraph query helper ─────────────────────────────────────────────────────
# Usage: mgquery <cypher_query>
# Returns: mgconsole output
mgquery() {
  local query="$1"
  echo "$query" | mgconsole -host "$MEMGRAPH_HOST" -port "$MEMGRAPH_PORT" 2>/dev/null
}
