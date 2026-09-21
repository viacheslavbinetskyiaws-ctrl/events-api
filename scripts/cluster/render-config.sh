#!/usr/bin/env bash
# Writes the (gitignored) env file that the cluster-config Kustomize
# component turns into the `cluster-config` ConfigMap. Every value comes from
# Terraform outputs handed in through the environment; nothing is hand-typed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_env ACCOUNT_ID AWS_REGION RDS_HOST RDS_MASTER_SECRET_ARN

target="${K8S_ROOT}/components/cluster-config-source/cluster-config.env"
cat > "${target}" <<EOF
ACCOUNT_ID=${ACCOUNT_ID}
ECR_REGISTRY=${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
RDS_HOST=${RDS_HOST}
AWS_REGION=${AWS_REGION}
RDS_MASTER_SECRET_ARN=${RDS_MASTER_SECRET_ARN}
EOF
log "wrote ${target}"
