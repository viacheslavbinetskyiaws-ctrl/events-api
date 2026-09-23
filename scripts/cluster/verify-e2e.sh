#!/usr/bin/env bash
# On-demand deep verification of a live cluster: the acceptance bar from
# docs/superpowers/specs/2026-09-19-ci-driven-bootstrap-design.md, runnable
# any time after a cluster-up, not just right after one.
#
# Deliberately separate from platform-up.sh's own stage_verify, which already
# runs automatically on every cluster-up (pods, connectors, dbt,
# data-quality, ALB reachability — fast, non-mutating). This script creates a
# real tenant and posts a real event, and takes real wall-clock time (SSE
# propagation, port-forwards), so it stays a manually-invoked check rather
# than something that blocks the deploy pipeline itself.
#
#   scripts/cluster/verify-e2e.sh
#
# Prerequisite: kubectl already pointed at the cluster (aws eks
# update-kubeconfig ...), same convention as platform-up.sh/platform-down.sh.
# Also needs: curl, python3, bq (authenticated against the project holding
# events_analytics — see AWS_PLAN.md's BigQuery/gcloud auth notes).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${here}/lib.sh"

kubectl get namespace "${NAMESPACE}" >/dev/null \
  || die "no '${NAMESPACE}' namespace — is kubectl pointed at the right cluster? (aws eks update-kubeconfig ...)"

TENANT_ID=""
EVENT_ID=""

stage_pods() {
  log "1/6 pod health"
  local bad
  bad="$(kubectl get pods -A --no-headers | grep -vE 'Running|Completed' || true)"
  [[ -z "${bad}" ]] || die $'unhealthy pods:\n'"${bad}"
}

stage_connectors() {
  log "2/6 Kafka connectors"
  retry_until 60 "all 4 connectors RUNNING" connectors_running
}

stage_data_quality() {
  log "3/6 data quality via the ALB, cross-checked against the latest dbt-build Job (not trusted blindly)"
  local resp
  resp="$(curl -fsS "http://$(alb_host)/health/data-quality")"

  NAMESPACE="${NAMESPACE}" DQ_REPORT="${resp}" python3 -c '
import json, os, subprocess, sys
from datetime import datetime

report = json.loads(os.environ["DQ_REPORT"])
if report.get("passed") is not True:
    sys.exit(f"data-quality report is not passed=true: {report}")

jobs = json.loads(subprocess.check_output(
    ["kubectl", "get", "jobs", "-n", os.environ["NAMESPACE"], "-o", "json"]
))["items"]
dbt_jobs = [
    j for j in jobs
    if j["metadata"]["name"].startswith("dbt-build") and j.get("status", {}).get("completionTime")
]
if not dbt_jobs:
    sys.exit("no completed dbt-build Job found to cross-check freshness against")
latest = max(j["status"]["completionTime"] for j in dbt_jobs)

parse = lambda ts: datetime.fromisoformat(ts.replace("Z", "+00:00"))
drift = abs((parse(report["generated_at"]) - parse(latest)).total_seconds())
if drift > 900:
    sys.exit(f"data-quality generated_at is {drift:.0f}s from the latest dbt-build completion ({latest}) - looks stale")
print(f"data-quality fresh: generated_at within {drift:.0f}s of the latest dbt-build Job")
'
}

extract_id() { python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }

stage_tenant_event_fanout() {
  log "4/6 tenant + event + 2-pod SSE fan-out (proves independent consumer groups, not just the Service)"
  local alb pods pod1 pod2 tmpdir pf1_pid pf2_pid s1_pid s2_pid

  alb="http://$(alb_host)"

  TENANT_ID="$(curl -fsS -X POST "${alb}/admin/tenants" \
    -H 'Content-Type: application/json' \
    -d '{"name": "verify-e2e", "plan_tier": "free"}' | extract_id)"
  [[ -n "${TENANT_ID}" ]] || die "tenant creation did not return an id"

  pods=($(kubectl get pods -n "${NAMESPACE}" -l app=realtime -o jsonpath='{.items[*].metadata.name}'))
  [[ "${#pods[@]}" -ge 2 ]] || die "expected at least 2 realtime pods for the fan-out proof, found ${#pods[@]}"
  pod1="${pods[0]}"; pod2="${pods[1]}"

  tmpdir="$(mktemp -d)"
  cleanup() { kill "${pf1_pid:-}" "${pf2_pid:-}" "${s1_pid:-}" "${s2_pid:-}" 2>/dev/null || true; rm -rf "${tmpdir}"; }
  trap cleanup RETURN

  kubectl port-forward -n "${NAMESPACE}" "${pod1}" 3001:3000 >/dev/null 2>&1 &
  pf1_pid=$!
  kubectl port-forward -n "${NAMESPACE}" "${pod2}" 3002:3000 >/dev/null 2>&1 &
  pf2_pid=$!
  retry_until 30 "port-forward to ${pod1}" curl -fsS --max-time 2 http://localhost:3001/healthz
  retry_until 30 "port-forward to ${pod2}" curl -fsS --max-time 2 http://localhost:3002/healthz

  curl -N -s -H "X-Tenant-ID: ${TENANT_ID}" http://localhost:3001/stream/events > "${tmpdir}/stream1.log" &
  s1_pid=$!
  curl -N -s -H "X-Tenant-ID: ${TENANT_ID}" http://localhost:3002/stream/events > "${tmpdir}/stream2.log" &
  s2_pid=$!
  sleep 2

  EVENT_ID="$(curl -fsS -X POST "${alb}/events" \
    -H 'Content-Type: application/json' -H "X-Tenant-ID: ${TENANT_ID}" \
    -d '{"event_type": "verify.e2e", "user_id": "verify-e2e", "properties": {}}' | extract_id)"
  [[ -n "${EVENT_ID}" ]] || die "event creation did not return an id"

  retry_until 30 "the event to appear on both SSE streams" \
    bash -c "grep -q '${EVENT_ID}' '${tmpdir}/stream1.log' && grep -q '${EVENT_ID}' '${tmpdir}/stream2.log'"

  diff "${tmpdir}/stream1.log" "${tmpdir}/stream2.log" \
    || die "the two realtime pods produced different SSE output for the same event - fan-out is broken"

  log "fan-out confirmed byte-identical across pods ${pod1} and ${pod2}"
}

mongo_projected() {
  kubectl logs -n "${NAMESPACE}" deploy/cdc-consumer --tail=500 | grep -q "${EVENT_ID}"
}

stage_mongo_projection() {
  log "5/6 Mongo projection (cdc-consumer's own log line for the event just posted)"
  retry_until 60 "cdc-consumer to log the projection for ${EVENT_ID}" mongo_projected
}

bigquery_has_event() {
  local count
  count="$(bq query --use_legacy_sql=false --format=csv -q \
    "SELECT COUNT(*) FROM \`events_analytics.cdc_events\` WHERE id = \"${EVENT_ID}\"" 2>/dev/null | tail -1)"
  [[ "${count}" == "1" ]]
}

stage_bigquery() {
  log "6/6 BigQuery sink (events_analytics.cdc_events row for the event just posted)"
  retry_until 120 "the event to land in BigQuery" bigquery_has_event
}

stage_pods
stage_connectors
stage_data_quality
stage_tenant_event_fanout
stage_mongo_projection
stage_bigquery
log "full workflow verified end-to-end (tenant ${TENANT_ID}, event ${EVENT_ID})"
