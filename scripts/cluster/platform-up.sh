#!/usr/bin/env bash
# cluster-up, Kubernetes half. Idempotent: every stage can be re-run against a
# live cluster (the bootstrap-chain Jobs are deleted and re-applied; the seed
# Job is only re-run if it has not succeeded).
#
#   scripts/cluster/platform-up.sh                # all stages, in order
#   scripts/cluster/platform-up.sh helm base      # only these stages
#
# Stages: config helm base bootstrap connect seed verify
#
# In CI the `infra` job exports ACCOUNT_ID, AWS_REGION, CLUSTER_NAME, RDS_HOST
# and RDS_MASTER_SECRET_ARN. On a laptop, set AWS_PROFILE and they are read
# from the cluster root's Terraform outputs.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${here}/lib.sh"

load_from_terraform
require_env ACCOUNT_ID AWS_REGION RDS_HOST RDS_MASTER_SECRET_ARN CLUSTER_NAME
HELM_TIMEOUT="${HELM_TIMEOUT:-10m}"

stage_config() {
  log "1/7 cluster-config and namespaces"
  "${here}/render-config.sh"
  local ns
  for ns in events-api monitoring; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
  done
}

stage_helm() {
  log "2/7 Helm releases and their CRDs"
  helm repo add eks https://aws.github.io/eks-charts >/dev/null
  helm repo add strimzi https://strimzi.io/charts/ >/dev/null
  helm repo add mongodb https://mongodb.github.io/helm-charts >/dev/null
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
  helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
  helm repo update >/dev/null

  # The ALB controller chart runs with serviceAccount.create=false, so its
  # IRSA-annotated ServiceAccount must exist before the chart is installed.
  apply_overlay aws-alb-controller

  helm_upgrade metrics-server kube-system metrics-server/metrics-server \
    --version 3.14.0 \
    -f "${REPO_ROOT}/helm/metrics-server/values-override.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm_upgrade aws-load-balancer-controller kube-system eks/aws-load-balancer-controller \
    --version 3.5.0 \
    -f "${REPO_ROOT}/helm/aws-load-balancer-controller/values-override.yaml" \
    --set clusterName="${CLUSTER_NAME}" --set region="${AWS_REGION}" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm_upgrade strimzi-kafka-operator events-api strimzi/strimzi-kafka-operator \
    --version 1.2.0 \
    -f "${REPO_ROOT}/helm/strimzi/values.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm_upgrade community-operator events-api mongodb/community-operator \
    --version 0.13.0 \
    -f "${REPO_ROOT}/helm/mongodb-community-operator/values-override.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm_upgrade kube-prometheus-stack monitoring prometheus-community/kube-prometheus-stack \
    --version 90.0.0 \
    -f "${REPO_ROOT}/helm/kube-prometheus-stack/values-override.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"

  local crd
  for crd in kafkas.kafka.strimzi.io kafkanodepools.kafka.strimzi.io \
    kafkaconnects.kafka.strimzi.io kafkaconnectors.kafka.strimzi.io \
    mongodbcommunity.mongodbcommunity.mongodb.com; do
    kubectl wait --for=condition=Established "crd/${crd}" --timeout=120s
  done
}

ensure_mongo_seed_password() {
  if kubectl get secret mongo-consumer-seed-password -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi
  # Generated locally and piped straight into kubectl: never held in a shell
  # variable, never echoed. Only created when absent, so re-running `up`
  # against a live cluster never rotates the operator's user password.
  openssl rand -base64 24 | tr -d '\n' \
    | kubectl create secret generic mongo-consumer-seed-password \
      -n "${NAMESPACE}" --from-file=password=/dev/stdin
}

stage_base() {
  log "3/7 base workloads: app, Kafka, Mongo, realtime, dbt, observability"
  # The bootstrap-chain Jobs are recreated (suspended) on every run. Deleting
  # first means a completed Job's spec is never mutated by a re-apply.
  local job
  for job in events-api-migrate events-api-bootstrap-master \
    events-api-bootstrap-roles aws-cdc-create-publications; do
    kubectl delete job "${job}" -n "${NAMESPACE}" --ignore-not-found --wait=true
  done
  ensure_mongo_seed_password
  apply_overlay aws-realtime
  apply_overlay aws-dbt
  apply_overlay aws-observability
}

stage_bootstrap() {
  log "4/7 database bootstrap: rds_iam grant -> migration -> role setup -> publications"
  apply_overlay aws-bootstrap
  unsuspend_and_wait events-api-bootstrap-master 600
  unsuspend_and_wait events-api-migrate 900
  unsuspend_and_wait events-api-bootstrap-roles 600
  unsuspend_and_wait aws-cdc-create-publications 600
}

stage_connect() {
  log "5/7 credentials, Kafka Connect and connectors"
  # Debezium password: the canonical value lives in Secrets Manager (set by
  # the bootstrap-roles Job); pipe it into the Kubernetes Secret.
  aws secretsmanager get-secret-value \
    --secret-id events-api/debezium-replication --region "${AWS_REGION}" \
    --query SecretString --output text \
    | tr -d '\n' \
    | kubectl create secret generic debezium-db-credentials \
      -n "${NAMESPACE}" --from-file=password=/dev/stdin --dry-run=client -o yaml \
    | kubectl apply -f -

  # Mongo: the operator generates events-mongo-admin-cdc-consumer once the
  # replica set is up; the consumer reads a copy of its connection string.
  # (go-template base64decode: no dependency on a platform-specific base64.)
  retry_until 1500 "the operator-generated Mongo connection Secret" \
    kubectl get secret events-mongo-admin-cdc-consumer -n "${NAMESPACE}"
  kubectl get secret events-mongo-admin-cdc-consumer -n "${NAMESPACE}" \
    -o go-template='{{index .data "connectionString.standard" | base64decode}}' \
    | kubectl create secret generic streaming-mongo-credentials \
      -n "${NAMESPACE}" --from-file=STREAMING_MONGO_URI=/dev/stdin --dry-run=client -o yaml \
    | kubectl apply -f -

  apply_overlay aws-connect
  kubectl wait kafkaconnect/events-connect -n "${NAMESPACE}" \
    --for=condition=Ready --timeout=20m
}

stage_seed() {
  log "6/7 seed data (once per successful cluster lifetime)"
  # Gate on SUCCESS, not on existence: a Job that exists but failed (retries
  # exhausted) must be recreated, or every later `up` would die here until
  # someone deleted it by hand. A re-run after a partial failure can leave a
  # few extra demo tenants; acceptable for disposable seed data.
  if [[ "$(kubectl get job events-api-seed -n "${NAMESPACE}" -o jsonpath='{.status.succeeded}' 2>/dev/null)" == "1" ]]; then
    echo "seed already completed; not re-running"
    return 0
  fi
  kubectl delete job events-api-seed -n "${NAMESPACE}" --ignore-not-found --wait=true
  apply_overlay aws-seed
  wait_job events-api-seed 900
}

data_quality_ok() {
  kubectl exec deploy/events-api -n "${NAMESPACE}" -- python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/data-quality', timeout=10)"
}

# alb_host and connectors_running are shared with verify-e2e.sh — see lib.sh.
alb_ok() {
  local host
  host="$(alb_host)"
  [[ -n "${host}" ]] && curl -fsS --max-time 10 "http://${host}/healthz"
}

stage_verify() {
  log "7/7 verify: workloads, connectors, dbt, data quality, ALB"
  # A consumer that started before its dependency was up sits in
  # CrashLoopBackOff (up to 5 minutes between retries) even after the
  # dependency is healthy. rollout status waits that out, and fails loudly if a
  # Deployment never becomes available, so a stuck pod cannot slip through.
  local d
  for d in events-api realtime cdc-consumer; do
    kubectl rollout status "deploy/${d}" -n "${NAMESPACE}" --timeout=10m
  done
  retry_until 900 "all 4 connectors RUNNING" connectors_running

  # /health/data-quality returns 503 until a dbt run has published a report
  # (fail-closed by design), so the run is required, not optional.
  local run
  run="dbt-build-bootstrap-$(date +%s)"
  kubectl create job "${run}" --from=cronjob/dbt-build -n "${NAMESPACE}"
  wait_job "${run}" 900
  retry_until 300 "/health/data-quality to return 200" data_quality_ok

  retry_until 900 "the ALB to serve /healthz" alb_ok
  log "cluster-up verification passed (ALB: http://$(alb_host))"
}

all_stages=(config helm base bootstrap connect seed verify)
if [[ $# -gt 0 ]]; then
  stages=("$@")
else
  stages=("${all_stages[@]}")
fi
for s in "${stages[@]}"; do
  case "${s}" in
    config | helm | base | bootstrap | connect | seed | verify) "stage_${s}" ;;
    *) die "unknown stage '${s}' (valid: ${all_stages[*]})" ;;
  esac
done
