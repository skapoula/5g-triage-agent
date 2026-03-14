#!/usr/bin/env bash
# phase4-e2e.sh — 4 E2E scenarios with inject/trigger/verify/restore
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"
resolve_triage_pod

log "=== Phase 4: End-to-End Validation ==="
ERRORS=0
SCENARIO_RESULTS=()

# ── Helper: verify RCA fields ─────────────────────────────────────────────────
# Usage: verify_rca <incident_id> <expected_root_nf_regex> <expected_layer> <scenario_label>
verify_rca() {
  local incident_id=$1 expected_nf_re=$2 expected_layer=$3 label=$4
  local report root_nf layer confidence fail_mode eq_score

  report=$(curl -s "$WEBHOOK_URL/incidents/$incident_id")
  root_nf=$(echo "$report"    | jq -r '.final_report.root_nf // ""')
  layer=$(echo "$report"      | jq -r '.final_report.layer // ""')
  confidence=$(echo "$report" | jq -r '.final_report.confidence // 0')
  fail_mode=$(echo "$report"  | jq -r '.final_report.failure_mode // ""')
  eq_score=$(echo "$report"   | jq -r '.final_report.evidence_quality_score // 0')

  log "  root_nf=$root_nf  layer=$layer  confidence=$confidence"
  log "  failure_mode=$fail_mode  evidence_quality=$eq_score"
  echo "$report" | jq . >> "$RESULTS_DIR/${label}-report.json"

  local ok=true
  [[ "$root_nf" =~ $expected_nf_re ]] || { fail "$label: root_nf=$root_nf not in [$expected_nf_re]"; ok=false; }
  [[ "$layer" == "$expected_layer" ]]  || { fail "$label: layer=$layer expected $expected_layer"; ok=false; }
  [[ "$fail_mode" != "llm_timeout" ]]  || { fail "$label: llm_timeout sentinel"; ok=false; }
  local conf_ok; conf_ok=$(echo "$confidence >= 0.70" | bc -l)
  [[ "$conf_ok" -eq 1 ]] || { fail "$label: confidence=$confidence < 0.70"; ok=false; }
  local eq_ok; eq_ok=$(echo "$eq_score >= 0.50" | bc -l)
  [[ "$eq_ok" -eq 1 ]] || { fail "$label: evidence_quality=$eq_score < 0.50"; ok=false; }

  $ok && { pass "$label PASSED (root_nf=$root_nf layer=$layer confidence=$confidence)"; return 0; }
  ERRORS=$((ERRORS+1)); return 1
}

# ── 4.1: Sunny Day ────────────────────────────────────────────────────────────
log ""
log "=== Scenario 4.1: Sunny Day ==="
INCIDENT_41=$(trigger_webhook "RegistrationFailures" "amf" "warning")
log "Incident: $INCIDENT_41"
REPORT_41=$(poll_incident "$INCIDENT_41" 360)

INFRA_41=$(echo "$REPORT_41" | jq -r '.final_report.infra_score // 1')
INFRA_OK=$(echo "$INFRA_41 < 0.3" | bc -l)
[[ "$INFRA_OK" -eq 1 ]] && pass "4.1: infra_score=$INFRA_41 < 0.3 (no false positive)" \
  || fail "4.1: infra_score=$INFRA_41 ≥ 0.3 — possible false positive"

FAIL_41=$(echo "$REPORT_41" | jq -r '.final_report.failure_mode // ""')
[[ "$FAIL_41" != "llm_timeout" ]] && pass "4.1: LLM responded without timeout" \
  || { fail "4.1: llm_timeout"; ERRORS=$((ERRORS+1)); }

# Record baseline token counts
log "Recording baseline token counts..."
for artifact in pre_filter_metrics.json post_filter_metrics.json \
                pre_filter_logs.json post_filter_logs.json; do
  collect_token_count "$INCIDENT_41" "$artifact"
done
pull_artifacts "$INCIDENT_41"
SCENARIO_RESULTS+=("4.1:SUNNY_DAY:infra_score=$INFRA_41")

# ── 4.2: Registration Failure (AMF scaled to 0) ───────────────────────────────
log ""
log "=== Scenario 4.2: Registration Failure (AMF → 0 replicas) ==="

log "Injecting failure: scaling amf to 0..."
kubectl scale deployment amf -n "$CORE_NS" --replicas=0
kubectl get pods -n "$CORE_NS" | grep amf | tee "$RESULTS_DIR/scenario42-inject.txt"
sleep 30

log "Restarting UERANSIM to trigger registration attempts against unavailable AMF..."
kubectl rollout restart deployment ueransim -n "$CORE_NS"
kubectl rollout status deployment ueransim -n "$CORE_NS"
sleep 15

INCIDENT_42=$(trigger_webhook "RegistrationFailures" "amf")
log "Incident: $INCIDENT_42"
REPORT_42=$(poll_incident "$INCIDENT_42" 360)

verify_rca "$INCIDENT_42" "^AMF$" "infrastructure" "4.2" && SCENARIO_RESULTS+=("4.2:REG_FAIL:PASS") || SCENARIO_RESULTS+=("4.2:REG_FAIL:FAIL")
pull_artifacts "$INCIDENT_42"

log "Restoring: scaling amf back to 1..."
kubectl scale deployment amf -n "$CORE_NS" --replicas=1
kubectl rollout status deployment amf -n "$CORE_NS"
sleep 15  # allow AMF to complete NRF registration

kubectl rollout restart deployment ueransim -n "$CORE_NS"
kubectl rollout status deployment ueransim -n "$CORE_NS"
sleep 30
log "Confirming UEs re-registered after restore..."
check_imsi_loki 3 || log "WARNING: not all IMSIs visible after restore — proceeding with caution"

# ── 4.3: Authentication Failure (wrong OPC key) ───────────────────────────────
log ""
log "=== Scenario 4.3: Authentication Failure (wrong op key) ==="

log "Backing up ue-config..."
kubectl get configmap ue-config -n "$CORE_NS" -o yaml \
  > "$RESULTS_DIR/ue-config-backup.yaml"

log "Injecting failure: patching op key to zeroed value..."
kubectl get configmap ue-config -n "$CORE_NS" -o yaml \
  | sed "s/op: '8e27b6af0e692e750f32667a3b14605d'/op: '00000000000000000000000000000000'/" \
  | kubectl apply -f -
# Verify
OP_PATCHED=$(kubectl get configmap ue-config -n "$CORE_NS" \
  -o jsonpath='{.data.ue-base\.yaml}' | grep "^op:" | head -1)
log "  op field after patch: $OP_PATCHED"

kubectl rollout restart deployment ueransim -n "$CORE_NS"
kubectl rollout status deployment ueransim -n "$CORE_NS"
sleep 30

INCIDENT_43=$(trigger_webhook "AuthenticationFailures" "ausf")
log "Incident: $INCIDENT_43"
REPORT_43=$(poll_incident "$INCIDENT_43" 360)

verify_rca "$INCIDENT_43" "^(AUSF|UDM)$" "application" "4.3" && SCENARIO_RESULTS+=("4.3:AUTH_FAIL:PASS") || SCENARIO_RESULTS+=("4.3:AUTH_FAIL:FAIL")
pull_artifacts "$INCIDENT_43"

log "Restoring: applying ue-config backup..."
kubectl apply -f "$RESULTS_DIR/ue-config-backup.yaml"
OP_RESTORED=$(kubectl get configmap ue-config -n "$CORE_NS" \
  -o jsonpath='{.data.ue-base\.yaml}' | grep "^op:" | head -1)
log "  op field after restore: $OP_RESTORED"

kubectl rollout restart deployment ueransim -n "$CORE_NS"
kubectl rollout status deployment ueransim -n "$CORE_NS"
sleep 30
log "Confirming UEs re-registered after restore..."
check_imsi_loki 3 || log "WARNING: not all IMSIs visible after restore"

# ── 4.4: PDU Session Failure (wrong APN) ──────────────────────────────────────
log ""
log "=== Scenario 4.4: PDU Session Failure (wrong APN) ==="

log "Injecting failure: patching APN to invalid-internet..."
kubectl get configmap ue-config -n "$CORE_NS" -o yaml \
  | sed "s/apn: 'internet'/apn: 'invalid-internet'/" \
  | kubectl apply -f -
APN_PATCHED=$(kubectl get configmap ue-config -n "$CORE_NS" \
  -o jsonpath='{.data.ue-base\.yaml}' | grep "apn:" | head -1)
log "  APN after patch: $APN_PATCHED"

kubectl rollout restart deployment ueransim -n "$CORE_NS"
kubectl rollout status deployment ueransim -n "$CORE_NS"
sleep 30

INCIDENT_44=$(trigger_webhook "PDUSessionFailures" "smf")
log "Incident: $INCIDENT_44"
REPORT_44=$(poll_incident "$INCIDENT_44" 360)

verify_rca "$INCIDENT_44" "^SMF$" "application" "4.4" && SCENARIO_RESULTS+=("4.4:PDU_FAIL:PASS") || SCENARIO_RESULTS+=("4.4:PDU_FAIL:FAIL")
pull_artifacts "$INCIDENT_44"

log "Restoring: applying ue-config backup (reuse from 4.3)..."
kubectl apply -f "$RESULTS_DIR/ue-config-backup.yaml"
APN_RESTORED=$(kubectl get configmap ue-config -n "$CORE_NS" \
  -o jsonpath='{.data.ue-base\.yaml}' | grep "apn:" | head -1)
log "  APN after restore: $APN_RESTORED"

kubectl rollout restart deployment ueransim -n "$CORE_NS"
kubectl rollout status deployment ueransim -n "$CORE_NS"

# ── Overall summary ───────────────────────────────────────────────────────────
log ""
log "=== Phase 4 Summary ==="
CORRECT=0
for result in "${SCENARIO_RESULTS[@]}"; do
  echo "  $result" | tee -a "$RESULTS_DIR/phase4-summary.txt"
done

# Count failure scenario successes (4.2, 4.3, 4.4 only — 4.1 is sunny day)
for result in "${SCENARIO_RESULTS[@]:1}"; do
  [[ "$result" == *":PASS" ]] && CORRECT=$((CORRECT+1)) || true
done
log "Failure scenario accuracy: $CORRECT / 3 correct (need ≥ 2)"

if [[ "$ERRORS" -eq 0 ]]; then
  pass "Phase 4 PASSED"
  exit 0
else
  fail "Phase 4: $ERRORS check(s) failed"
  cat "$RESULTS_DIR/summary.txt"
  exit 1
fi
