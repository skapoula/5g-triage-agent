#!/usr/bin/env bash
# run-all.sh — execute all test phases in order; gate each phase on pass
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create a single timestamped results directory shared across all phases
export RESULTS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)/test-results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RESULTS_DIR"
echo "Results directory: $RESULTS_DIR"

source "$SCRIPT_DIR/helpers.sh"

# Require port-forward to be running
log "Checking webhook is reachable at $WEBHOOK_URL..."
curl -s --max-time 5 "$WEBHOOK_URL/health" > /dev/null || {
  echo "ERROR: $WEBHOOK_URL not reachable."
  echo "Run in a separate terminal: kubectl port-forward -n monitoring svc/triage-agent 8000:8000"
  exit 1
}

log "Starting full test run. Results: $RESULTS_DIR"

"$SCRIPT_DIR/phase0-preflight.sh"   || { echo "GATE: Phase 0 failed — aborting"; exit 1; }
"$SCRIPT_DIR/phase1-health.sh"      || { echo "GATE: Phase 1 failed — aborting"; exit 1; }
"$SCRIPT_DIR/phase2-components.sh"  || { echo "GATE: Phase 2 failed — aborting"; exit 1; }
"$SCRIPT_DIR/phase3-integration.sh" || { echo "GATE: Phase 3 failed — aborting"; exit 1; }
"$SCRIPT_DIR/phase4-e2e.sh"

echo ""
echo "=== FINAL RESULTS ==="
cat "$RESULTS_DIR/summary.txt"

echo ""
echo "Full results in: $RESULTS_DIR"
