#!/usr/bin/env bash
# Shared helpers for scripts/cluster/*.sh. Sourced, never executed.

NAMESPACE="${NAMESPACE:-events-api}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
K8S_ROOT="${REPO_ROOT}/k8s"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_env() {
  local name
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || die "required environment variable ${name} is not set"
  done
}

# Local-run convenience: fill any unset cluster variable from the cluster
# root's Terraform outputs. In CI the `infra` job exports them, so nothing
# here runs; on a laptop (AWS_PROFILE set) it makes the script one command.
load_from_terraform() {
  local tf="${REPO_ROOT}/terraform/cluster" out
  command -v terraform >/dev/null 2>&1 || return 0
  fill() { # fill <VAR> <terraform-output-name>
    if [[ -z "${!1:-}" ]]; then
      out="$(terraform -chdir="${tf}" output -raw "$2" 2>/dev/null)" || return 0
      printf -v "$1" '%s' "${out}"
      export "${1?}"
    fi
  }
  fill ACCOUNT_ID account_id
  fill AWS_REGION region
  fill CLUSTER_NAME cluster_name
  fill RDS_HOST rds_address
  fill RDS_MASTER_SECRET_ARN rds_master_user_secret_arn
}

# retry_until <timeout-seconds> <description> <command...>
# Re-runs the command every 10s until it succeeds. Output is discarded while
# waiting so a probe can never leak anything sensitive; on timeout the command
# is run once more, visibly, so the actual error is on screen.
retry_until() {
  local timeout="$1" what="$2" waited=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    if (( waited >= timeout )); then
      echo "last attempt of: $*" >&2
      "$@" 2>&1 | tail -20 >&2 || true
      die "timed out after ${timeout}s waiting for ${what}"
    fi
    sleep 10
    waited=$(( waited + 10 ))
  done
}

# wait_job <name> <timeout-seconds>: returns when the Job completes; fails
# immediately (with the pod logs) if it fails, instead of waiting out the
# timeout the way `kubectl wait --for=condition=complete` would.
wait_job() {
  local name="$1" timeout="$2" waited=0
  while (( waited < timeout )); do
    if [[ "$(kubectl get job "${name}" -n "${NAMESPACE}" -o jsonpath='{.status.succeeded}')" == "1" ]]; then
      echo "job/${name} completed"
      return 0
    fi
    if [[ "$(kubectl get job "${name}" -n "${NAMESPACE}" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}')" == "True" ]]; then
      echo "job/${name} failed; last log lines:" >&2
      kubectl logs "job/${name}" -n "${NAMESPACE}" --all-containers --tail=50 >&2 || true
      return 1
    fi
    sleep 5
    waited=$(( waited + 5 ))
  done
  echo "job/${name} did not finish within ${timeout}s; last log lines:" >&2
  kubectl logs "job/${name}" -n "${NAMESPACE}" --all-containers --tail=50 >&2 || true
  return 1
}

# unsuspend_and_wait <job> <timeout-seconds>
unsuspend_and_wait() {
  kubectl patch job "$1" -n "${NAMESPACE}" --type merge -p '{"spec":{"suspend":false}}' >/dev/null
  wait_job "$1" "$2"
}

apply_overlay() {
  kubectl apply -k "${K8S_ROOT}/overlays/$1"
}
