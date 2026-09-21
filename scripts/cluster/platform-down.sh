#!/usr/bin/env bash
# cluster-down, Kubernetes half. Order matters:
#  1. Ingress first, while the ALB controller still runs: it created the ALB
#     out-of-band, so Terraform has no record of it and would orphan it.
#  2. The Strimzi/Mongo custom resources next, while their operators still
#     run: their pods hold the PVCs, and a PVC in use stays Terminating.
#  3. Then the PVCs, so the EBS CSI driver releases the volumes.
# Idempotent: every step tolerates "already gone".
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "1/3 delete the Ingress and wait for the ALB controller to remove the ALB"
kubectl delete ingress events-api -n "${NAMESPACE}" --ignore-not-found --wait=true --timeout=10m

log "2/3 delete the Strimzi and Mongo custom resources"
for pair in \
  kafkaconnector:kafkaconnectors.kafka.strimzi.io \
  kafkaconnect:kafkaconnects.kafka.strimzi.io \
  kafka:kafkas.kafka.strimzi.io \
  kafkanodepool:kafkanodepools.kafka.strimzi.io \
  mongodbcommunity:mongodbcommunity.mongodbcommunity.mongodb.com; do
  kind="${pair%%:*}"
  crd="${pair#*:}"
  if kubectl get crd "${crd}" >/dev/null 2>&1; then
    kubectl delete "${kind}" --all -n "${NAMESPACE}" --ignore-not-found --wait=true --timeout=20m
  fi
done

log "3/3 delete PVCs"
kubectl delete pvc --all -n "${NAMESPACE}" --ignore-not-found --wait=true --timeout=10m
kubectl get pvc -A
