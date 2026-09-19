# CI-Driven Bootstrap, Plan 1 of 2: Terraform Foundations

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repo is a learning project: default to teaching mode** (`CLAUDE.md`). The person running this plan types the commands and makes the edits; an assistant hands over the exact content, explains why, then reads files/output back to check. Do not run mutating commands or write project files on their behalf unless they say so for that stretch of work. Do not `git commit` unless told to in that turn: every "Checkpoint" step below is the user's to run.

**Goal:** Split Terraform into roots by lifecycle so CI can destroy and recreate the whole ephemeral stack without touching the identities CI stands on, add the `bootstrap` identity and the Debezium secret container, and migrate the live cluster into the new layout.

**Architecture:** Five roots: `terraform/` (foundation: state bucket, ECR, GitHub OIDC roles, budget, SNS, secret container), `terraform/cluster/` (networking, EKS, RDS, IAM/IRSA, alarm), `terraform/gcp/` (GCP WIF for Kafka Connect: applied locally at first because CI has no GCP credentials, then by CI once Task 9 adds keyless GCP access) and `terraform/gcp-bootstrap/` (Task 9's trust anchor for that access: local-only permanently, because it grants project-level IAM that CI must never be able to grant itself). The old root's ephemeral modules are removed in one PR and destroyed by the existing manual `apply`; the new `cluster/` root is created by CI afterwards.

**Tech Stack:** Terraform `~> 1.15.8`, AWS provider `~> 6.62.0`, Google provider `~> 8.3.0`, GitHub Actions, S3-native state locking.

**Spec:** `docs/superpowers/specs/2026-09-19-ci-driven-bootstrap-design.md` (sections D1, D3, D4, D8). Plan 2 (`2026-09-19-ci-bootstrap-2-cluster-lifecycle.md`) builds on this one and must not start before Task 8 here passes. Task order: 1 (GCP relocation, local), 1b (land the baseline), 2-3 (foundation identities), 4-6 (cluster root, workflows, image matrix), 7 (cutover), 8 (first CI-created cluster), 9 (keyless GCP access for CI; independent of Plan 2, do it any time after Task 8).

## Global Constraints

- Terraform `required_version = "~> 1.15.8"`; AWS provider `~> 6.62.0`; Google provider `~> 8.3.0`.
- Region `eu-central-1`; account `938500344309`; real AWS CLI/Terraform calls use `AWS_PROFILE=events-api-tf` (never the default profile).
- State bucket `events-api-tfstate-938500344309`. State keys: foundation `events-api/terraform.tfstate`, cluster `events-api/cluster.tfstate`, gcp `events-api/gcp.tfstate`. All use `use_lockfile = true`.
- Name prefixes: `events-api` (networking/eks/rds/ecr/s3), `events-api-iam` (iam module), `events-api-github` (github-oidc module). EKS cluster name is `events-api-eks`.
- Persistent, never destroyed by `down`: S3 state bucket, ECR repos, GitHub OIDC roles, GCP WIF, budget, SNS topic + subscription, the Debezium secret container. Ephemeral: networking, eks, rds, iam (IRSA/viewer roles), the dbt-failed alarm.
- `terraform/cluster/` and `terraform/` must never depend on each other's resources in both directions. The foundation root computes the cluster ARN from the fixed cluster name; the cluster root looks up foundation identities by name.
- Secret values never appear in Terraform state, workflow logs or committed files. The Debezium secret is created as an empty container.
- No static AWS credentials in GitHub. `bootstrap` trusts `github_trust` (`ref:refs/heads/main` only); infra jobs reuse `environment: aws-infra` with `terraform_apply`.
- Shell is zsh: always brace `${VAR}` before a colon (`${IMAGE}:tag`), or zsh mangles it.
- Do not state an expected plan/apply count without having read the real output. Acceptance criteria below are stated as conditions to check, not predictions.
- Format with `terraform fmt -recursive terraform` before every checkpoint.
- Land the uncommitted baseline (Task 1b) before any new work is committed; every later PR's `plan` must be readable on its own.

## File Structure

| File | Responsibility |
|---|---|
| `terraform/gcp/{backend,versions,providers,main,imports}.tf` (new) | Third root: GCP WIF module (local-only until Task 9, then CI-managed) |
| `terraform/main.tf` (modify) | Foundation root: drop `gcp_wif`; then drop `networking`/`iam`/`eks`/`rds`; add name-derived cluster ARN, secret container wiring |
| `terraform/debezium_secret.tf` (new) | Empty Secrets Manager container for the Debezium password |
| `terraform/modules/github-oidc/{main,variables,outputs}.tf` (modify) | New `bootstrap` role; plan-lock covers cluster state key |
| `terraform/cluster/{backend,versions,providers,main,data,observability,outputs}.tf` (new) | Ephemeral root |
| `terraform/modules/eks/{main,variables}.tf` (modify) | `bootstrap` access entry + cluster-admin association |
| `terraform/modules/iam/{main,variables,outputs}.tf` (modify) | Two bootstrap-Job IRSA roles |
| `.github/workflows/terraform.yaml` (modify) | Plan both AWS roots on PR; `stack` input on manual apply |
| `.github/workflows/ci.yaml` (modify) | `build-push` becomes a matrix incl. `kafka-connect` |
| `terraform/gcp-bootstrap/`, `terraform/modules/gcp_github_wif/` (new, Task 9) | GitHub-OIDC -> GCP trust anchor, local-only |

---

### Task 1: Relocate GCP WIF into its own root (local-only until Task 9)

**Why first:** with no Google credentials, `terraform plan` on the current root fails. Verified read-only on 2026-09-19 by planning `-target=module.gcp_wif` with `HOME` pointed at an empty directory: `Error: Attempted to load application default credentials ... could not find default credentials`. CI has no GCP credentials, so committing the `google` provider into the root CI plans/applies breaks `terraform.yaml`. The S3 state already holds the four GCP resources (`terraform state list | grep -c module.gcp_wif` printed `4`), while `main`'s code does not declare them.

**Files:**
- Create: `terraform/gcp/backend.tf`, `terraform/gcp/versions.tf`, `terraform/gcp/providers.tf`, `terraform/gcp/main.tf`, `terraform/gcp/imports.tf`
- Modify: `terraform/main.tf`, `terraform/providers.tf`, `terraform/versions.tf`, `terraform/.terraform.lock.hcl`
- Delete: `terraform/import_gcp_wif.tf`

**Interfaces:**
- Produces: a `terraform/gcp/` root whose state key is `events-api/gcp.tfstate` and which manages the same four resources through `module.gcp_wif`; a foundation root with no Google provider.

- [ ] **Step 1: Back up the foundation state (outside the repo)**

```bash
mkdir -p ~/tf-state-backups
AWS_PROFILE=events-api-tf terraform -chdir=terraform state pull > ~/tf-state-backups/foundation-$(date +%Y%m%d-%H%M).json
ls -l ~/tf-state-backups/
```
Expected: a non-empty `.json` file. (State can contain sensitive values; never commit it.)

- [ ] **Step 2: Create the gcp root**

`terraform/gcp/backend.tf`:
```hcl
terraform {
  backend "s3" {
    bucket       = "events-api-tfstate-938500344309"
    key          = "events-api/gcp.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}
```

`terraform/gcp/versions.tf`:
```hcl
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.3.0"
    }
  }

  required_version = "~> 1.15.8"
}
```

`terraform/gcp/providers.tf`:
```hcl
provider "google" {
  project = "project-e8569bd6-524d-42fe-bb9"
}
```

`terraform/gcp/main.tf`:
```hcl
# Applied locally (Google Application Default Credentials: `gcloud auth
# application-default login`) until Task 9 gives CI keyless GCP access; until
# then it is deliberately absent from .github/workflows/terraform.yaml's
# plan/apply matrix. Everything here is permanent (prevent_destroy) and
# changes rarely.
module "gcp_wif" {
  source = "../modules/gcp_wif"
}
```

`terraform/gcp/imports.tf` (copy of the four blocks in `terraform/import_gcp_wif.tf`, unchanged):
```hcl
import {
  to = module.gcp_wif.google_iam_workload_identity_pool.kafka_connect
  id = "kafka-connect-pool"
}

import {
  to = module.gcp_wif.google_iam_workload_identity_pool_provider.eks_kafka_connect
  id = "kafka-connect-pool/eks-kafka-connect"
}

import {
  to = module.gcp_wif.google_service_account_iam_member.workload_identity_user
  id = "projects/project-e8569bd6-524d-42fe-bb9/serviceAccounts/big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com roles/iam.workloadIdentityUser principalSet://iam.googleapis.com/projects/10216729029/locations/global/workloadIdentityPools/kafka-connect-pool/attribute.aws_role/events-api-eks-node-kafka-connect"
}

import {
  to = module.gcp_wif.google_service_account_iam_member.token_creator
  id = "projects/project-e8569bd6-524d-42fe-bb9/serviceAccounts/big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com roles/iam.serviceAccountTokenCreator principalSet://iam.googleapis.com/projects/10216729029/locations/global/workloadIdentityPools/kafka-connect-pool/attribute.aws_role/events-api-eks-node-kafka-connect"
}
```

- [ ] **Step 3: Init and validate the gcp root**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp init
terraform -chdir=terraform/gcp validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 4: Forget the GCP resources from the foundation state (does not touch GCP)**

Do **not** run `terraform plan` in `terraform/` between this step and Step 7: until the code is edited, it would try to create them.

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform state rm module.gcp_wif
AWS_PROFILE=events-api-tf terraform -chdir=terraform state list | grep -c gcp_wif
```
Expected: the `state rm` output lists 4 removed objects; the `grep -c` prints `0`.

- [ ] **Step 5: Adopt them in the gcp root**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp plan
```
Read the whole output. Acceptance: every one of the four resources is `will be imported`, and there are no add/change/destroy actions. (Milestone 12 recorded exactly this shape for the same import IDs.) If anything else appears, stop and diagnose before continuing.

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp apply
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp plan
```
Acceptance: the second plan prints `No changes.`

- [ ] **Step 6: Confirm the BigQuery sinks are unaffected**

```bash
kubectl get kafkaconnector -n events-api
```
Acceptance: `bigquery-events-sink` and `bigquery-tenant-accounts-sink` are still `READY: True` (only meaningful while the current cluster is still live).

- [ ] **Step 7: Remove GCP from the foundation root**

Edit `terraform/main.tf`: delete the final block
```hcl
module "gcp_wif" {
  source = "./modules/gcp_wif"
}
```
Edit `terraform/providers.tf`: delete
```hcl
provider "google" {
  project = "project-e8569bd6-524d-42fe-bb9"
}
```
Edit `terraform/versions.tf`: delete the `google = { ... }` entry under `required_providers`, leaving only `aws`.

```bash
git rm -f terraform/import_gcp_wif.tf
git add terraform/modules/gcp_wif/main.tf
```
(The module directory stays: `terraform/gcp/` uses it. The second command just makes sure it is tracked.)

- [ ] **Step 8: Re-lock and verify the foundation root is clean**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform init
git diff terraform/.terraform.lock.hcl
```
If a `provider "registry.terraform.io/hashicorp/google"` block is still present in the lock file, delete that block by hand (it is only a lock entry). Then:

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform plan
```
Acceptance: `No changes.` and no Google credentials needed. Prove the second half by re-running it credential-less:
```bash
REALHOME=$HOME; EMPTY=$(mktemp -d); env -u GOOGLE_APPLICATION_CREDENTIALS HOME=${EMPTY} AWS_CONFIG_FILE=${REALHOME}/.aws/config AWS_SHARED_CREDENTIALS_FILE=${REALHOME}/.aws/credentials AWS_PROFILE=events-api-tf terraform -chdir=terraform plan; rm -rf ${EMPTY}
```
Acceptance: `No changes.` with no credential error.

- [ ] **Step 9: Delete the now-redundant import blocks in the gcp root**

They have done their job (state already holds the resources). `git rm -f terraform/gcp/imports.tf` (or plain `rm` if it was never staged), then `terraform -chdir=terraform/gcp plan` again and confirm `No changes.`

- [ ] **Step 10: Do not commit yet**

```bash
terraform fmt -recursive terraform
git status --short
```
The working tree now holds everything from the Milestone 11/12 session plus this relocation. Task 1b lands it as one reviewed baseline.

---

### Task 1b: Land the baseline: the uncommitted Milestone 11/12 work plus the GCP relocation

**Why:** since 2026-09-14 the working tree has carried about two dozen uncommitted files describing changes that are already live: the `dbt deps` `Dockerfile` fix, all five Helm values files, the RDS WAL parameters, the EKS addon resources, the Kafka Connect heap setting and `terraform/modules/gcp_wif/`. `main` disagrees with the live account, and CI builds and deploys from `main`, so the dbt image CI builds today is still the broken one. Every later task must be reviewable against a baseline where code equals live state. This comes **after** Task 1 (not before) because a baseline that still contained the `google` provider could not have a clean CI `plan`: CI has no Google credentials.

**Files:** none new: this task commits what already exists.

- [ ] **Step 1: Inspect what is about to be committed**

```bash
git status --short
```
Read every line. Leave `.mcp.json` out (it is local tooling config and has always been untracked). Everything else in the list belongs to the Milestone 11/12 session, this initiative's spec and plans, or Task 1's relocation.

- [ ] **Step 2: Branch and stage**

```bash
git switch -c ci-bootstrap-baseline
git add -A
git reset -- .mcp.json
git status --short
```
Acceptance: `.mcp.json` is not staged; nothing else is unstaged.

- [ ] **Step 3: Commit (user runs), preferably as two commits for reviewability**

Suggested split: first `git reset` then stage only `terraform/gcp terraform/modules/gcp_wif terraform/providers.tf terraform/versions.tf terraform/main.tf terraform/.terraform.lock.hcl` and commit `Move GCP WIF to its own local-only Terraform root`; then `git add -A && git reset -- .mcp.json` and commit `Land Milestone 11/12 work and the CI-bootstrap spec and plans`. A single commit is also acceptable.

- [ ] **Step 4: Open the PR and read its automatic `plan`**

```bash
git push -u origin ci-bootstrap-baseline
```
Acceptance from the PR checks: `test` is green, and the foundation `plan` job reports **`No changes.`** This is the check that matters: it proves `main` plus this PR describes exactly what is live, and that CI can plan without Google credentials. If it reports changes, the code and the live account still differ: read them, decide which side is right, and fix before merging.

- [ ] **Step 5: Merge and confirm the images**

Merge the PR. The `CI` run on `main` then builds and pushes the four images (the matrix and `kafka-connect` arrive in Task 6). Confirm the dbt image is fresh:
```bash
aws ecr describe-images --repository-name events-api-dbt --image-ids imageTag=latest --profile events-api-tf --region eu-central-1 --query 'imageDetails[0].imagePushedAt' --output text
git switch main && git pull
```
Acceptance: the timestamp is from just now, and `main` contains the `RUN cd dbt && uv run --extra dbt dbt deps` line in `Dockerfile`.

---

### Task 2: Decouple the foundation root from the EKS cluster

**Branch:** `git switch main && git pull && git switch -c ci-bootstrap-identities` (Tasks 2 and 3 share this branch and one PR).

**Files:**
- Modify: `terraform/main.tf` (add `data`/`locals`, change the `github_oidc` block's `eks_cluster_arn`)

**Interfaces:**
- Produces: `local.eks_cluster_arn` in the foundation root, equal to the value `module.eks.cluster_arn` has today, computed without reading any ephemeral resource.

- [ ] **Step 1: Add the lookups and the derived ARN**

Insert at the top of `terraform/main.tf`:
```hcl
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Must equal "${name_prefix}-eks" as derived inside modules/eks (see
  # terraform/cluster/main.tf, which uses the same literal prefix). Computed
  # here instead of read from module.eks so this root never depends on an
  # ephemeral resource: CI must be able to destroy the cluster without
  # touching, or being unable to plan, the identities it runs on.
  cluster_name    = "events-api-eks"
  eks_cluster_arn = "arn:aws:eks:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:cluster/${local.cluster_name}"
}
```

- [ ] **Step 2: Use it in the `github_oidc` block**

Change `eks_cluster_arn  = module.eks.cluster_arn` to:
```hcl
  eks_cluster_arn  = local.eks_cluster_arn
```

- [ ] **Step 3: Verify it is a pure refactor**

```bash
terraform fmt terraform
AWS_PROFILE=events-api-tf terraform -chdir=terraform validate
AWS_PROFILE=events-api-tf terraform -chdir=terraform plan
```
Acceptance: `No changes.` (the computed string equals the resource's ARN, so the deploy policy is untouched). If a change to `aws_iam_policy.deploy_describe_cluster` appears, compare the two ARN strings and fix the local before continuing.

---

### Task 3: Foundation additions: `bootstrap` role, Debezium secret container, kafka-connect ECR access

**Files:**
- Create: `terraform/debezium_secret.tf`
- Modify: `terraform/main.tf`, `terraform/outputs.tf`, `terraform/modules/github-oidc/main.tf`, `terraform/modules/github-oidc/variables.tf`, `terraform/modules/github-oidc/outputs.tf`

**Interfaces:**
- Produces: IAM role `events-api-github-bootstrap` (output `github_bootstrap_role_arn`), secret `events-api/debezium-replication` (output `debezium_secret_arn`), `ecr_push` able to push `events-api-kafka-connect`, `terraform_plan` able to lock `events-api/cluster.tfstate`.
- Consumed later: `terraform/cluster/` looks up the role and secret **by these exact names**.

- [ ] **Step 1: Create the secret container**

`terraform/debezium_secret.tf`:
```hcl
# Container only. The value is generated and stored at runtime by the
# cluster-up bootstrap Job (Plan 2) and never passes through Terraform, so it
# never lands in state. Persistent by design: keeping the same password across
# teardown cycles means each `up` only has to ALTER ROLE to match it.
resource "aws_secretsmanager_secret" "debezium" {
  name        = "events-api/debezium-replication"
  description = "Password for the debezium_replication Postgres role. Value written by the cluster-up bootstrap Job, not by Terraform."

  lifecycle {
    prevent_destroy = true
  }
}
```

- [ ] **Step 2: Add the module variable**

Append to `terraform/modules/github-oidc/variables.tf`:
```hcl
variable "debezium_secret_arn" {
  type = string
}
```

- [ ] **Step 3: Add the `bootstrap` role**

Append to `terraform/modules/github-oidc/main.tf`:
```hcl
# --- bootstrap: dispatch-only cluster lifecycle identity (cluster-up/down) ---
# Trusts github_trust (ref:refs/heads/main), the same policy deploy and
# ecr_push use: a workflow_dispatch run on any other branch carries a
# different sub claim and cannot assume this role, so "main only" is enforced
# by IAM itself, with no Environment protection rule needed. AWS-side it can
# only describe the cluster (for `aws eks update-kubeconfig`) and read the
# Debezium secret; its real authority is the cluster-admin EKS access entry
# created in terraform/cluster (modules/eks).

resource "aws_iam_role" "bootstrap" {
  name               = "${var.name_prefix}-bootstrap"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "bootstrap" {
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [var.eks_cluster_arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.debezium_secret_arn]
  }
}

resource "aws_iam_policy" "bootstrap" {
  name   = "${var.name_prefix}-bootstrap-policy"
  policy = data.aws_iam_policy_document.bootstrap.json
}

resource "aws_iam_role_policy_attachment" "bootstrap" {
  role       = aws_iam_role.bootstrap.name
  policy_arn = aws_iam_policy.bootstrap.arn
}
```

- [ ] **Step 4: Let `terraform_plan` lock the cluster state key**

In `terraform/modules/github-oidc/main.tf`, in `data "aws_iam_policy_document" "terraform_plan_state_lock"`, replace
```hcl
    resources = ["${var.state_bucket_arn}/events-api/terraform.tfstate.tflock"]
```
with
```hcl
    resources = [
      "${var.state_bucket_arn}/events-api/terraform.tfstate.tflock",
      "${var.state_bucket_arn}/events-api/cluster.tfstate.tflock",
    ]
```
and extend the comment above it: "Key paths match each root's backend.tf `key` literally (foundation and cluster); backend blocks cannot reference variables, so this coupling cannot be a shared value."

- [ ] **Step 5: Output the role**

Append to `terraform/modules/github-oidc/outputs.tf`:
```hcl
output "bootstrap_role_arn" {
  value = aws_iam_role.bootstrap.arn
}
```

- [ ] **Step 6: Wire it in the foundation root**

In `terraform/main.tf`'s `module "github_oidc"` block: add `debezium_secret_arn = aws_secretsmanager_secret.debezium.arn`, and add the fifth ECR repo to the list:
```hcl
  ecr_repository_arns = [
    module.ecr.repository_arns["app"],
    module.ecr.repository_arns["streaming"],
    module.ecr.repository_arns["dbt"],
    module.ecr.repository_arns["realtime"],
    module.ecr.repository_arns["kafka-connect"],
  ]
```
Append to `terraform/outputs.tf`:
```hcl
output "github_bootstrap_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes for cluster-up/cluster-down Kubernetes work"
  value       = module.github_oidc.bootstrap_role_arn
}

output "debezium_secret_arn" {
  description = "Secrets Manager container for the debezium_replication password"
  value       = aws_secretsmanager_secret.debezium.arn
}
```

- [ ] **Step 7: Plan and read it**

```bash
terraform fmt -recursive terraform
AWS_PROFILE=events-api-tf terraform -chdir=terraform validate
AWS_PROFILE=events-api-tf terraform -chdir=terraform plan
```
Acceptance, read from the real output:
1. Additions include exactly these kinds of things: `aws_secretsmanager_secret.debezium`, `module.github_oidc.aws_iam_role.bootstrap`, its `aws_iam_policy` and `aws_iam_role_policy_attachment`.
2. In-place updates are limited to `module.github_oidc.aws_iam_policy.ecr_push` and `...terraform_plan_state_lock`.
3. **Zero** destroys, zero replacements.

Record the real `X to add, Y to change, 0 to destroy` line in `WHATS_NEXT.md` when you next update it.

- [ ] **Step 8: Checkpoint and land it (user runs)**

Commit Tasks 2 and 3 on `ci-bootstrap-identities` and open the PR. The automatic `plan` job runs on it (using the existing single-root workflow; Task 5 changes it later). Read its plan against Step 7's acceptance: because the baseline landed in Task 1b, this PR carries only Tasks 2-3, so the plan reads cleanly. Merge only if it matches. Then dispatch the existing manual apply: GitHub → Actions → **Terraform** → Run workflow (branch `main`).

- [ ] **Step 9: Verify what was applied**

```bash
aws iam get-role --role-name events-api-github-bootstrap --profile events-api-tf --query 'Role.Arn' --output text
aws secretsmanager describe-secret --secret-id events-api/debezium-replication --profile events-api-tf --query '[Name,length(VersionIdsToStages||`{}`)]' --output text
```
Acceptance: the role ARN prints; the secret prints its name and a version count of `0` (an empty container).

---

### Task 4: The `cluster/` root and the module changes it needs

**Branch:** `git switch main && git pull && git switch -c ci-bootstrap-cluster-root` (Tasks 4-6 share this branch; Task 7 opens its PR). Task 3 must already be merged **and applied**.

**Files:**
- Create: `terraform/cluster/backend.tf`, `versions.tf`, `providers.tf`, `main.tf`, `data.tf`, `observability.tf`, `outputs.tf`
- Modify: `terraform/modules/eks/main.tf`, `terraform/modules/eks/variables.tf`, `terraform/modules/iam/main.tf`, `terraform/modules/iam/variables.tf`, `terraform/modules/iam/outputs.tf`
- Modify (remove the ephemeral half from the foundation root): `terraform/main.tf`, `terraform/outputs.tf`, `terraform/observability.tf`

**Interfaces:**
- Consumes: role `events-api-github-deploy`, role `events-api-github-bootstrap`, secret `events-api/debezium-replication`, SNS topic `events-api-dbt-build-alerts` (all looked up **by name**; Task 3 must be applied first).
- Produces (outputs Plan 2 reads): `cluster_name`, `region`, `account_id`, `rds_address`, `rds_endpoint`, `rds_master_user_secret_arn`, plus every output the old root had for the ephemeral modules.

- [ ] **Step 1: Root plumbing**

`terraform/cluster/backend.tf`:
```hcl
terraform {
  backend "s3" {
    bucket       = "events-api-tfstate-938500344309"
    key          = "events-api/cluster.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}
```

`terraform/cluster/versions.tf`:
```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.62.0"
    }
  }

  required_version = "~> 1.15.8"
}
```

`terraform/cluster/providers.tf`:
```hcl
provider "aws" {
  region = "eu-central-1"
}
```

- [ ] **Step 2: Lookups of persistent things (by name)**

`terraform/cluster/data.tf`:
```hcl
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Persistent identities and resources owned by the foundation root
# (terraform/). Looked up by their fixed names so this root depends on them
# one-way and can be destroyed without touching them.
data "aws_iam_role" "github_deploy" {
  name = "events-api-github-deploy"
}

data "aws_iam_role" "github_bootstrap" {
  name = "events-api-github-bootstrap"
}

data "aws_secretsmanager_secret" "debezium" {
  name = "events-api/debezium-replication"
}

data "aws_sns_topic" "dbt_build_alerts" {
  name = "events-api-dbt-build-alerts"
}
```

- [ ] **Step 3: The modules, moved from the foundation root**

`terraform/cluster/main.tf`:
```hcl
module "networking" {
  source = "../modules/networking"

  vpc_cidr    = "10.0.0.0/16"
  name_prefix = "events-api"
}

module "iam" {
  source = "../modules/iam"

  name_prefix           = "events-api-iam"
  oidc_provider_arn     = module.eks.oidc_provider_arn
  oidc_provider_url     = module.eks.oidc_provider_url
  k8s_namespace         = "events-api"
  k8s_service_account   = "events-api-app"
  rds_resource_id       = module.rds.resource_id
  rds_db_user           = "events_app"
  rds_master_secret_arn = module.rds.master_user_secret_arn
  debezium_secret_arn   = data.aws_secretsmanager_secret.debezium.arn
}

module "eks" {
  source = "../modules/eks"

  # The cluster is named "${name_prefix}-eks" inside modules/eks. The
  # foundation root (terraform/main.tf) computes its cluster ARN from the
  # literal "events-api-eks", so this prefix must not change without
  # changing that local too.
  name_prefix            = "events-api"
  cluster_subnet_ids     = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids        = module.networking.private_subnet_ids
  k8s_viewer_role_arn    = module.iam.k8s_viewer_role_arn
  github_deploy_role_arn = data.aws_iam_role.github_deploy.arn
  bootstrap_role_arn     = data.aws_iam_role.github_bootstrap.arn
}

module "rds" {
  source = "../modules/rds"

  name_prefix               = "events-api"
  vpc_id                    = module.networking.vpc_id
  subnet_ids                = module.networking.private_subnet_ids
  cluster_security_group_id = module.eks.cluster_security_group_id
}
```

- [ ] **Step 4: The alarm moves here**

`terraform/cluster/observability.tf` (the alarm resource from `terraform/observability.tf`, action target now looked up):
```hcl
# Lives with the cluster because the metric it watches is published by the
# dbt CronJob: with treat_missing_data = "breaching", a torn-down cluster
# would otherwise fire ALARM (and, on the next `up`, OK) emails. The SNS
# topic and its email subscription stay in the foundation root so the
# subscription is confirmed once, not on every `up`.
resource "aws_cloudwatch_metric_alarm" "dbt_build_failed" {
  alarm_name          = "events-api-dbt-build-failed"
  alarm_description   = "dbt CronJob build failed, or didn't run at all this hour"
  namespace           = "EventsApi/DataQuality"
  metric_name         = "DbtBuildPassed"
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [data.aws_sns_topic.dbt_build_alerts.arn]
  ok_actions          = [data.aws_sns_topic.dbt_build_alerts.arn]
}
```
Before saving, open `terraform/observability.tf` and confirm every argument above still matches the original alarm there (it is the source of truth; do not invent arguments).

- [ ] **Step 5: Outputs**

`terraform/cluster/outputs.tf`:
```hcl
output "cluster_name" {
  description = "EKS cluster name, for aws eks update-kubeconfig"
  value       = module.eks.cluster_name
}

output "region" {
  value = data.aws_region.current.region
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "rds_address" {
  description = "Postgres hostname without port: what manifests and Jobs use as the DB host"
  value       = module.rds.address
}

output "rds_endpoint" {
  description = "Postgres connection endpoint (address:port)"
  value       = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "Secrets Manager ARN holding the RDS master password (read only by bootstrap Job 1)"
  value       = module.rds.master_user_secret_arn
}

output "eks_oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

output "eks_oidc_provider_url" {
  value = module.eks.oidc_provider_url
}

output "kafka_connect_node_role_arn" {
  value = module.eks.kafka_connect_node_role_arn
}

output "app_irsa_role_arn" {
  value = module.iam.app_irsa_role_arn
}

output "kafka_connect_gcp_irsa_role_arn" {
  value = module.iam.kafka_connect_gcp_irsa_role_arn
}

output "migration_irsa_role_arn" {
  value = module.iam.migration_irsa_role_arn
}

output "dbt_irsa_role_arn" {
  value = module.iam.dbt_irsa_role_arn
}

output "k8s_viewer_role_arn" {
  value = module.iam.k8s_viewer_role_arn
}

output "alb_controller_role_arn" {
  value = module.iam.alb_controller_role_arn
}

output "bootstrap_master_irsa_role_arn" {
  value = module.iam.bootstrap_master_irsa_role_arn
}

output "bootstrap_roles_irsa_role_arn" {
  value = module.iam.bootstrap_roles_irsa_role_arn
}
```

- [ ] **Step 6: EKS module: `bootstrap` access entry**

Append to `terraform/modules/eks/variables.tf`:
```hcl
variable "bootstrap_role_arn" {
  type        = string
  description = "GitHub OIDC bootstrap role: granted EKS cluster-admin so cluster-up/down can install Helm charts and apply manifests"
}
```
Append to `terraform/modules/eks/main.tf`, next to the existing `creator`/`root` entries:
```hcl
resource "aws_eks_access_entry" "bootstrap" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.bootstrap_role_arn
}

resource "aws_eks_access_policy_association" "bootstrap_admin" {
  cluster_name = aws_eks_cluster.this.name
  # Referencing the entry's attribute (not the variable) creates the
  # dependency edge: the association API fails if the entry does not exist yet.
  principal_arn = aws_eks_access_entry.bootstrap.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}
```

- [ ] **Step 7: IAM module: the two bootstrap-Job IRSA roles**

Append to `terraform/modules/iam/variables.tf`:
```hcl
variable "rds_master_secret_arn" {
  type = string
}

variable "debezium_secret_arn" {
  type = string
}
```
Append to `terraform/modules/iam/main.tf` (uses the module's existing `data.aws_region.current` and `data.aws_caller_identity.current`):
```hcl
# --- bootstrap Job 1: reads the RDS-managed master secret ---
# The first `GRANT rds_iam TO events` on a fresh instance can only authenticate
# with the master password. The secret uses the AWS-managed
# aws/secretsmanager KMS key (verified 2026-09-19), so no kms:Decrypt
# statement is included; if the first run proves one is needed, add it here.

data "aws_iam_policy_document" "bootstrap_master_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-api-bootstrap-master"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "bootstrap_master_irsa" {
  name               = "${var.name_prefix}-bootstrap-master-irsa"
  assume_role_policy = data.aws_iam_policy_document.bootstrap_master_irsa_trust.json
}

data "aws_iam_policy_document" "bootstrap_master_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.rds_master_secret_arn]
  }
}

resource "aws_iam_policy" "bootstrap_master_secret" {
  name   = "${var.name_prefix}-bootstrap-master-secret"
  policy = data.aws_iam_policy_document.bootstrap_master_secret.json
}

resource "aws_iam_role_policy_attachment" "bootstrap_master_secret" {
  role       = aws_iam_role.bootstrap_master_irsa.name
  policy_arn = aws_iam_policy.bootstrap_master_secret.arn
}

# --- bootstrap Job 2: post-migration grants + the Debezium credential ---

data "aws_iam_policy_document" "bootstrap_roles_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-api-bootstrap-roles"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "bootstrap_roles_irsa" {
  name               = "${var.name_prefix}-bootstrap-roles-irsa"
  assume_role_policy = data.aws_iam_policy_document.bootstrap_roles_irsa_trust.json
}

data "aws_iam_policy_document" "bootstrap_roles" {
  # Connects as the owner role over IAM (its rds_iam grant landed in Job 1).
  statement {
    actions   = ["rds-db:connect"]
    resources = ["arn:aws:rds-db:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:dbuser:${var.rds_resource_id}/events"]
  }

  # Scoped to the one secret: create the first value, read it back.
  statement {
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
    ]
    resources = [var.debezium_secret_arn]
  }

  # GetRandomPassword does not support resource-level permissions.
  statement {
    actions   = ["secretsmanager:GetRandomPassword"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "bootstrap_roles" {
  name   = "${var.name_prefix}-bootstrap-roles"
  policy = data.aws_iam_policy_document.bootstrap_roles.json
}

resource "aws_iam_role_policy_attachment" "bootstrap_roles" {
  role       = aws_iam_role.bootstrap_roles_irsa.name
  policy_arn = aws_iam_policy.bootstrap_roles.arn
}
```
Append to `terraform/modules/iam/outputs.tf`:
```hcl
output "bootstrap_master_irsa_role_arn" {
  value = aws_iam_role.bootstrap_master_irsa.arn
}

output "bootstrap_roles_irsa_role_arn" {
  value = aws_iam_role.bootstrap_roles_irsa.arn
}
```

- [ ] **Step 8: Init, lock for CI's platform, validate**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster init
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster providers lock -platform=linux_amd64 -platform=darwin_arm64
terraform fmt -recursive terraform
terraform -chdir=terraform/cluster validate
```
Acceptance: `Success! The configuration is valid.` and `terraform/cluster/.terraform.lock.hcl` exists (CI runs on linux/amd64; the second command adds those hashes).

- [ ] **Step 9: Plan the cluster root (do NOT apply)**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster plan
```
The foundation root still owns the live ephemeral resources in state, so this plan proposes to create everything. That is expected and harmless: read it for errors only. Acceptance: it reaches the end of planning with no `Error:` (in particular no "not found" from a `data` lookup, which would mean Task 3 was not applied).

- [ ] **Step 10: Remove the ephemeral half from the foundation root**

This is the same change Terraform needs for CI to tear the stack down: resources that are in state but no longer in configuration are destroyed by the next `apply`. Nothing is applied by this task.

In `terraform/main.tf` delete the blocks `module "networking"`, `module "iam"`, `module "eks"` and `module "rds"`. Keep `s3`, `ecr`, `github_oidc`, the `data`/`locals` from Task 2 and the secret wiring.

In `terraform/outputs.tf` delete these outputs (they now live in `terraform/cluster/outputs.tf`): `eks_cluster_name`, `eks_oidc_provider_arn`, `eks_oidc_provider_url`, `rds_endpoint`, `rds_master_user_secret_arn`, `app_irsa_role_arn`, `kafka_connect_gcp_irsa_role_arn`, `kafka_connect_node_role_arn`, `migration_irsa_role_arn`, `dbt_irsa_role_arn`, `k8s_viewer_role_arn`, `alb_controller_role_arn`. Keep `state_bucket_id`, `ecr_repository_urls`, the four existing `github_*` outputs and the two added in Task 3.

In `terraform/observability.tf` delete only `aws_cloudwatch_metric_alarm.dbt_build_failed`; keep `aws_sns_topic.dbt_build_alerts` and `aws_sns_topic_subscription.dbt_build_alerts_email`.

- [ ] **Step 11: Read what the foundation root would now destroy**

```bash
terraform fmt -recursive terraform
AWS_PROFILE=events-api-tf terraform -chdir=terraform validate
AWS_PROFILE=events-api-tf terraform -chdir=terraform plan -no-color > ~/tf-state-backups/migration-plan.txt
grep 'will be destroyed' ~/tf-state-backups/migration-plan.txt | grep -vE 'module\.(networking|eks|rds|iam)\.|aws_cloudwatch_metric_alarm\.dbt_build_failed'
```
Acceptance: **the `grep` prints nothing.** Any line it prints is a persistent resource (S3, ECR, OIDC roles, SNS, secret, budget) scheduled for destruction: stop and fix the configuration before going on. Then find the final `Plan:` line in `~/tf-state-backups/migration-plan.txt` and record it. Do not apply, and do not dispatch the foundation `apply` workflow until Task 7 says so: from here until then the foundation root's code no longer matches the live cluster.

- [ ] **Step 12: Checkpoint (user runs)**

```bash
terraform fmt -recursive terraform
git add terraform
git status --short
```
Commit on branch `ci-bootstrap-cluster-root` (message: `Add terraform/cluster root; move ephemeral modules out of the foundation root`). The PR is opened in Task 7, after the Kubernetes-side teardown.

---

### Task 5: `terraform.yaml`: plan both AWS roots, choose the root on apply

**Files:**
- Modify: `.github/workflows/terraform.yaml`

**Interfaces:**
- Produces: a `stack` dispatch input (`foundation` | `cluster`); PR plans for both roots. Plan 2's `cluster-up`/`cluster-down` do their own Terraform steps and do not use this input; it exists for incremental changes to a live cluster (spec capability 1).

- [ ] **Step 1: Replace the file**

```yaml
name: Terraform

on:
  pull_request:
    paths: ["terraform/**"]
  workflow_dispatch:
    inputs:
      stack:
        description: Terraform root to apply
        type: choice
        options:
          - foundation
          - cluster
        default: foundation

permissions:
  contents: read
  id-token: write

jobs:
  plan:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - stack: foundation
            dir: terraform
          - stack: cluster
            dir: terraform/cluster
    env:
      TF_VAR_budget_notification_email: ${{ secrets.BUDGET_NOTIFICATION_EMAIL }}
      TF_VAR_use_locastack: "false"
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_TERRAFORM_PLAN_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: hashicorp/setup-terraform@v4
        with:
          terraform_version: "1.15.8"
      - run: |
          cd ${{ matrix.dir }}
          terraform init
          terraform plan

  apply:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    environment: aws-infra
    env:
      TF_VAR_budget_notification_email: ${{ secrets.BUDGET_NOTIFICATION_EMAIL }}
      TF_VAR_use_locastack: "false"
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_TERRAFORM_APPLY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: hashicorp/setup-terraform@v4
        with:
          terraform_version: "1.15.8"
      - run: |
          if [ "${{ inputs.stack }}" = "cluster" ]; then
            cd terraform/cluster
          else
            cd terraform
          fi
          terraform init
          terraform apply -auto-approve
```
`TF_VAR_*` values for variables the cluster root does not declare are expected to be ignored by Terraform (environment-supplied values for undeclared variables produce no error, unlike `-var`/tfvars). The first PR run confirms this; if the `cluster` plan job errors on them, move both variables out of the job-level `env:` and into the foundation-only step.

- [ ] **Step 2: Lint the workflow**

If `actionlint` is installed (`brew install actionlint`), run:
```bash
actionlint .github/workflows/terraform.yaml
```
Acceptance: no output. Otherwise the PR run in Task 7 is the check.

- [ ] **Step 3: Checkpoint (user runs)**

```bash
git add .github/workflows/terraform.yaml
git status --short
```
Commit on `ci-bootstrap-cluster-root` (message: `Plan both AWS Terraform roots on PRs; add stack input to apply`). Do not open the PR yet.

---

### Task 6: `build-push` as a matrix, adding `kafka-connect`

**Files:**
- Modify: `.github/workflows/ci.yaml` (the `build-push` job only)

**Interfaces:**
- Consumes: the `ecr_push` role now covering `events-api-kafka-connect` (Task 3).
- Produces: five images per merge to `main`, each tagged `${{ github.sha }}` and `latest`.

- [ ] **Step 1: Verify the repo names first**

```bash
aws ecr describe-repositories --profile events-api-tf --region eu-central-1 --query 'repositories[].repositoryName' --output text
```
Acceptance: the output includes `events-api-app`, `events-api-streaming`, `events-api-dbt`, `events-api-realtime` and `events-api-kafka-connect`. If a name differs, use the real one in the matrix below.

- [ ] **Step 2: Replace the `build-push` job**

```yaml
  build-push:
    needs: test
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    strategy:
      fail-fast: false
      matrix:
        include:
          - repo: events-api-app
            context: .
            target: runtime
          - repo: events-api-streaming
            context: .
            target: runtime-streaming
          - repo: events-api-dbt
            context: .
            target: runtime-dbt
          - repo: events-api-realtime
            context: ./realtime
            target: runtime
          - repo: events-api-kafka-connect
            context: ./kafka-connect
            target: ""
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_ECR_PUSH_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr
      - uses: docker/setup-qemu-action@v4
      - uses: docker/setup-buildx-action@v4
      - uses: docker/build-push-action@v7
        with:
          context: ${{ matrix.context }}
          target: ${{ matrix.target }}
          platforms: linux/arm64
          push: true
          tags: |
            ${{ steps.ecr.outputs.registry }}/${{ matrix.repo }}:${{ github.sha }}
            ${{ steps.ecr.outputs.registry }}/${{ matrix.repo }}:latest
```
Leave `test` and `deploy` exactly as they are. `kafka-connect/Dockerfile` has no named final stage, so an empty `target` builds its last stage.

- [ ] **Step 3: Checkpoint (user runs), then first-run evidence after Task 7 merges**

```bash
git add .github/workflows/ci.yaml
git status --short
```
Commit on `ci-bootstrap-cluster-root` (message: `Build all five images from a matrix, including kafka-connect`). After Task 7's PR merges, watch the `CI` run on `main`. Acceptance: all five matrix legs are green, and then

```bash
aws ecr describe-images --repository-name events-api-kafka-connect --image-ids imageTag=latest --profile events-api-tf --region eu-central-1 --query 'imageDetails[0].[imagePushedAt,imageTags]' --output text
```
shows a fresh push. **Known risk:** the Kafka Connect base images (`debezium/connect`, `quay.io/strimzi/kafka`) have only been built for arm64 by hand so far; if the QEMU build of that leg fails, read its log, fix that leg only (`platforms` is per-leg-overridable by adding a `platforms:` key to that matrix entry), and do not block the other four.

Also confirm the dbt fix landed: the `events-api-dbt` leg builds from the `Dockerfile` containing `RUN cd dbt && uv run --extra dbt dbt deps` (line 80).

---

### Task 7: Cutover: tear down the live stack and merge the split

**This task destroys the running cluster and the RDS instance (no final snapshot). All data is lost, by design.** Do it only when Tasks 1-6 are committed on their branches and Task 3 is applied, and you are ready to lose everything.

**Files:** none (this task performs the teardown and lands the branch from Tasks 4-6).

- [ ] **Step 1: Preconditions**

```bash
git switch ci-bootstrap-cluster-root
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster validate
aws iam get-role --role-name events-api-github-bootstrap --profile events-api-tf --query 'Role.RoleName' --output text
aws secretsmanager describe-secret --secret-id events-api/debezium-replication --profile events-api-tf --query Name --output text
```
Acceptance: `Success! The configuration is valid.`, the role name, and the secret name all print (Task 3 is applied).

- [ ] **Step 2: Kubernetes-side teardown, by hand, once**

The `bootstrap` access entry does not exist on the old cluster, so use your own admin kubeconfig:
```bash
aws eks update-kubeconfig --name events-api-eks --region eu-central-1 --profile events-api-tf
kubectl delete ingress events-api -n events-api --wait=true --timeout=10m
aws elbv2 describe-load-balancers --profile events-api-tf --region eu-central-1 --query 'LoadBalancers[].LoadBalancerName' --output text
```
Acceptance: the last command prints nothing. (If the `kubectl delete` returned but a load balancer remains, wait a minute and re-run the `aws` command; the controller deletes it asynchronously. Do not continue until it is gone.)

A PVC that a running pod still uses stays in `Terminating` forever, so stop the pods first by deleting the CRs that own them while both operators are still running (the operators process the deletions):
```bash
kubectl delete kafkaconnector,kafkaconnect,kafka,kafkanodepool,mongodbcommunity --all -n events-api --wait=true --timeout=15m
kubectl get pods -n events-api
```
Acceptance: no Kafka, Kafka Connect or Mongo pods remain (the app, realtime and consumer pods may still be running; they hold no PVC). On 2026-09-19 the live cluster had exactly three PVCs, all in `events-api` (`data-0-events-dual-role-0`, `data-volume-events-mongo-0`, `logs-volume-events-mongo-0`), and Prometheus had none. Re-check with the first command below, then delete them:
```bash
kubectl get pvc -A
kubectl delete pvc --all -n events-api --wait=true --timeout=10m
kubectl get pvc -A
```
If `kubectl get pvc -A` lists claims in other namespaces, delete them the same way (`-n <namespace>`). Then wait for the volumes to disappear:
```bash
aws ec2 describe-volumes --profile events-api-tf --region eu-central-1 --filters "Name=tag:kubernetes.io/cluster/events-api-eks,Values=owned" --query 'Volumes[].VolumeId' --output text
```
Acceptance: prints nothing. If volumes remain after a few minutes, the StorageClass did not default to `Delete`; delete them by hand with `aws ec2 delete-volume --volume-id <id>` and note the finding in `WHATS_NEXT.md`.

- [ ] **Step 3: Open the PR (user runs)**

```bash
git push -u origin ci-bootstrap-cluster-root
```
(Use the repo's `github-aws-personal` SSH remote; `git remote -v` should show it.) Open the PR on GitHub. Read both automatic `plan` jobs:
- `foundation`: the destroy list must match what you read in Task 4 Step 11, and contain no persistent resource.
- `cluster`: a full create, no `Error:`.

Also confirm the `test` job is green. Merge only when both plans are read and understood.

- [ ] **Step 4: Destroy the old ephemeral stack through the pipeline**

After the merge: GitHub → Actions → **Terraform** → Run workflow, `stack = foundation`, branch `main`. This apply destroys the ephemeral stack (EKS and RDS deletion take several minutes; wait for the run to finish).

- [ ] **Step 5: Verify it is gone and the persistent layer survived**

```bash
export AWS_PROFILE=events-api-tf AWS_REGION=eu-central-1
aws eks list-clusters --query 'clusters' --output text
aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' --output text
aws ec2 describe-nat-gateways --filter Name=state,Values=available --query 'NatGateways[].NatGatewayId' --output text
aws ec2 describe-vpcs --filters Name=tag:Name,Values=events-api-vpc --query 'Vpcs[].VpcId' --output text
aws ecr describe-repositories --query 'repositories[].repositoryName' --output text
aws iam get-role --role-name events-api-github-bootstrap --query 'Role.RoleName' --output text
aws secretsmanager describe-secret --secret-id events-api/debezium-replication --query Name --output text
```
Acceptance: the first four print nothing; the last three print the five ECR repo names, `events-api-github-bootstrap` and `events-api/debezium-replication`.

- [ ] **Step 6: Confirm the merged `CI` run**

Watch the `CI` run triggered by the merge (Task 6 Step 3): five green matrix legs, a fresh `events-api-kafka-connect:latest`, and the dbt image built from the `Dockerfile` that contains `RUN cd dbt && uv run --extra dbt dbt deps`.

---

### Task 8: First cluster creation through CI (infra only)

**Files:** none.

- [ ] **Step 1: Apply the cluster root through the pipeline**

GitHub → Actions → **Terraform** → Run workflow, `stack = cluster`. Wait for it to finish (this creates the VPC, NAT, EKS, node groups, addons, RDS and IRSA roles from nothing).

- [ ] **Step 2: Verify**

```bash
export AWS_PROFILE=events-api-tf AWS_REGION=eu-central-1
aws eks describe-cluster --name events-api-eks --query 'cluster.status' --output text
aws eks list-access-entries --cluster-name events-api-eks --query 'accessEntries' --output text
aws rds describe-db-instances --db-instance-identifier events-api-db --query 'DBInstances[0].DBInstanceStatus' --output text
terraform -chdir=terraform/cluster output -raw rds_address
```
Acceptance: `ACTIVE`; the access-entry list includes `.../events-api-github-bootstrap`; `available`; the RDS hostname prints. Compare that hostname with the one hardcoded in the manifests (`events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com`) and record whether it survived a full teardown: this is the fact Plan 2's ConfigMap approach makes unnecessary to rely on, but it is worth writing down in `WHATS_NEXT.md`.

- [ ] **Step 3: Both roots are clean**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform plan
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster plan
```
Acceptance: both print `No changes.`

- [ ] **Step 4: Decide what to leave running**

If you are continuing straight into Plan 2 today, leave it up (Plan 2's `cluster-up` `infra` job is idempotent against a live `cluster/` root). Otherwise tear it down now, locally, because there are no workloads and therefore no ALB or PVC to clean up first:
```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster destroy
```
(the `cluster-down` workflow replaces this command in Plan 2).

- [ ] **Step 5: Update the resumption log (user runs)**

Add a short entry to `WHATS_NEXT.md` recording: the GCP-relocation finding, the real plan counts from Tasks 3, 7 and 8, whether the RDS hostname survived, and the `kafka-connect` QEMU build result.

### Task 9: Keyless GCP access for CI (GitHub OIDC -> Workload Identity Federation)

**Why:** Task 1 put GCP WIF in its own root because CI had no Google credentials. CI already reaches AWS with no stored secrets (GitHub's OIDC token exchanged for a role); Google supports the same shape. This task gives CI that access so `terraform/gcp/` joins the pipeline. It is independent of Plan 2: do it after Task 8, in parallel with or after Plan 2.

**Design (verified 2026-09-19 unless marked):**
- Pool + OIDC provider (issuer `https://token.actions.githubusercontent.com`) with a mandatory attribute condition pinning the numeric IDs (`assertion.repository_id`, `assertion.repository_owner_id`; both documented GitHub OIDC claims). Numeric IDs, because GitHub's `sub` format already changed once (Milestone 10, bug 2).
- Two service accounts, mirroring AWS's `terraform_plan` / `terraform_apply` split. Each is impersonable only from one exact `sub`: `...:pull_request` for plan, `...:environment:aws-infra` for apply (the same strings the AWS trust policies already accept). Impersonation grant: `roles/iam.workloadIdentityUser` on the service account (the indirect route; Google's docs say direct principalSet grants are not supported by every resource).
- Roles (each checked with `gcloud iam roles describe`): plan = `roles/iam.workloadIdentityPoolViewer` (project) + `roles/iam.serviceAccountViewer` (which includes `getIamPolicy`) on the `big-query` service account; apply = `roles/iam.workloadIdentityPoolAdmin` (project) + `roles/iam.serviceAccountAdmin` on the `big-query` service account only.
- **Two GCP roots, on purpose.** A Terraform-managed `google_project_iam_member` needs project-level IAM admin, which would let CI grant itself anything. So the trust anchor (GitHub pool/provider, the two service accounts, their project-level grants) lives in **`terraform/gcp-bootstrap/`**, applied locally and rarely, exactly like a one-time bootstrap. CI manages only `terraform/gcp/` (the existing Kafka Connect WIF resources). Service-account-level grants (on `big-query`) sit in the bootstrap root too, so CI can never widen its own access.
- Residual risk, equal to AWS's `terraform_apply` today: an Environment with no protection rules can be declared by a workflow on any branch, and this repo is solo-owned. A CEL-mapped attribute combining `sub` and `ref` could pin apply to `main` later.
- Not enabling APIs speculatively: `iam.googleapis.com` and `sts.googleapis.com` do not appear in `gcloud services list --enabled`, yet Milestone 12 imported and planned these resources, so IAM calls work. Enable one (`gcloud services enable <name>`) only if a first run reports "API not enabled".

**Files:**
- Create: `terraform/modules/gcp_github_wif/{main,variables,outputs}.tf`, `terraform/gcp-bootstrap/{backend,versions,providers,main,outputs}.tf`
- Modify: `terraform/gcp/main.tf` (comment), `terraform/modules/github-oidc/main.tf` (plan-lock key), `.github/workflows/terraform.yaml`

**Interfaces:**
- Produces outputs from the bootstrap root: `workload_identity_provider` (full resource name), `plan_service_account_email`, `apply_service_account_email`, stored as repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_PLAN_SERVICE_ACCOUNT`, `GCP_APPLY_SERVICE_ACCOUNT`.
- Consumes: the numeric owner/repo IDs already used in `terraform/main.tf`'s `github_oidc` block (`327975409`, `1366376677`).

- [ ] **Step 1: The module**

`terraform/modules/gcp_github_wif/variables.tf`:
```hcl
variable "project_id" {
  type = string
}

variable "github_owner" {
  type = string
}

variable "github_owner_id" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "github_repo_id" {
  type = string
}
```

`terraform/modules/gcp_github_wif/main.tf`:
```hcl
locals {
  # Exact OIDC `sub` claims: the same immutable-ID shapes the AWS trust
  # policies in modules/github-oidc already accept (verified live in
  # Milestone 10). Pull-request runs are branch-agnostic; the apply job
  # declares environment: aws-infra.
  repo_subject  = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}"
  plan_subject  = "${local.repo_subject}:pull_request"
  apply_subject = "${local.repo_subject}:environment:aws-infra"

  bigquery_service_account = "projects/${var.project_id}/serviceAccounts/big-query@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
  }

  # Mandatory: without a condition any GitHub repository's token could enter
  # the pool. Numeric IDs, not names, because names can be renamed or reused.
  attribute_condition = "assertion.repository_id == '${var.github_repo_id}' && assertion.repository_owner_id == '${var.github_owner_id}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account" "terraform_plan" {
  account_id   = "terraform-plan"
  display_name = "Terraform plan (GitHub Actions pull_request, read-only)"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_service_account" "terraform_apply" {
  account_id   = "terraform-apply"
  display_name = "Terraform apply (GitHub Actions, environment aws-infra)"

  lifecycle {
    prevent_destroy = true
  }
}

# Impersonation: only the exact subject may act as each service account.
resource "google_service_account_iam_member" "plan_impersonation" {
  service_account_id = google_service_account.terraform_plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/${local.plan_subject}"
}

resource "google_service_account_iam_member" "apply_impersonation" {
  service_account_id = google_service_account.terraform_apply.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/${local.apply_subject}"
}

# Project-level: workload identity pools have no IAM policy of their own, so
# these two roles can only be granted on the project. This is exactly why the
# module lives in a root CI never applies.
resource "google_project_iam_member" "plan_pool_viewer" {
  project = var.project_id
  role    = "roles/iam.workloadIdentityPoolViewer"
  member  = "serviceAccount:${google_service_account.terraform_plan.email}"
}

resource "google_project_iam_member" "apply_pool_admin" {
  project = var.project_id
  role    = "roles/iam.workloadIdentityPoolAdmin"
  member  = "serviceAccount:${google_service_account.terraform_apply.email}"
}

# Scoped to the one service account terraform/gcp manages IAM members on.
resource "google_service_account_iam_member" "plan_reads_bigquery_sa" {
  service_account_id = local.bigquery_service_account
  role               = "roles/iam.serviceAccountViewer"
  member             = "serviceAccount:${google_service_account.terraform_plan.email}"
}

resource "google_service_account_iam_member" "apply_administers_bigquery_sa" {
  service_account_id = local.bigquery_service_account
  role               = "roles/iam.serviceAccountAdmin"
  member             = "serviceAccount:${google_service_account.terraform_apply.email}"
}
```

`terraform/modules/gcp_github_wif/outputs.tf`:
```hcl
output "workload_identity_provider" {
  description = "Full provider resource name, for google-github-actions/auth"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "plan_service_account_email" {
  value = google_service_account.terraform_plan.email
}

output "apply_service_account_email" {
  value = google_service_account.terraform_apply.email
}
```

- [ ] **Step 2: The bootstrap root (local-only, permanently)**

`terraform/gcp-bootstrap/backend.tf`:
```hcl
terraform {
  backend "s3" {
    bucket       = "events-api-tfstate-938500344309"
    key          = "events-api/gcp-bootstrap.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}
```
`terraform/gcp-bootstrap/versions.tf`: identical to `terraform/gcp/versions.tf` (Google provider `~> 8.3.0`, `required_version = "~> 1.15.8"`).

`terraform/gcp-bootstrap/providers.tf`:
```hcl
provider "google" {
  project = "project-e8569bd6-524d-42fe-bb9"
}
```
`terraform/gcp-bootstrap/main.tf`:
```hcl
# The trust anchor for CI's GCP access. Applied LOCALLY, by a human, and
# rarely: it grants project-level IAM, which CI must never be able to do to
# itself. CI manages terraform/gcp only.
module "gcp_github_wif" {
  source = "../modules/gcp_github_wif"

  project_id      = "project-e8569bd6-524d-42fe-bb9"
  github_owner    = "viacheslavbinetskyiaws-ctrl"
  github_owner_id = "327975409"
  github_repo     = "events-api"
  github_repo_id  = "1366376677"
}
```
`terraform/gcp-bootstrap/outputs.tf`:
```hcl
output "workload_identity_provider" {
  value = module.gcp_github_wif.workload_identity_provider
}

output "plan_service_account_email" {
  value = module.gcp_github_wif.plan_service_account_email
}

output "apply_service_account_email" {
  value = module.gcp_github_wif.apply_service_account_email
}
```

- [ ] **Step 3: Init, validate, plan (read it all)**

```bash
terraform fmt -recursive terraform
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp-bootstrap init
terraform -chdir=terraform/gcp-bootstrap validate
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp-bootstrap plan
```
Acceptance: only additions: one pool, one provider, two service accounts, two impersonation members, two project members and two `big-query` service-account members; no changes to existing GCP resources and no destroys. If `plan` reports a resource-not-found on `big-query`, confirm the service account still exists (`gcloud iam service-accounts list`).

- [ ] **Step 4: Apply locally, once**

```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp-bootstrap apply
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp-bootstrap plan
```
Acceptance: the second plan prints `No changes.` If the apply reports an API not enabled, enable exactly that API with `gcloud services enable <name> --project=project-e8569bd6-524d-42fe-bb9`, then re-run.

- [ ] **Step 5: Publish the identifiers as repository variables**

```bash
cd terraform/gcp-bootstrap
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "$(AWS_PROFILE=events-api-tf terraform output -raw workload_identity_provider)" --repo viacheslavbinetskyiaws-ctrl/events-api
gh variable set GCP_PLAN_SERVICE_ACCOUNT --body "$(AWS_PROFILE=events-api-tf terraform output -raw plan_service_account_email)" --repo viacheslavbinetskyiaws-ctrl/events-api
gh variable set GCP_APPLY_SERVICE_ACCOUNT --body "$(AWS_PROFILE=events-api-tf terraform output -raw apply_service_account_email)" --repo viacheslavbinetskyiaws-ctrl/events-api
cd ../..
gh variable list --repo viacheslavbinetskyiaws-ctrl/events-api
```
Acceptance: the three variables are listed. None is a secret (identifiers only).

- [ ] **Step 6: Let the plan job lock the gcp state key**

In `terraform/modules/github-oidc/main.tf`, extend the `terraform_plan_state_lock` `resources` list with
```hcl
      "${var.state_bucket_arn}/events-api/gcp.tfstate.tflock",
```
(the local-only bootstrap root is never planned in CI, so it needs no entry). Lock the gcp root's providers for CI's platform:
```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp providers lock -platform=linux_amd64 -platform=darwin_arm64
```
Also update the comment in `terraform/gcp/main.tf` to say the root is planned and applied by CI (Task 9) as well as locally.

- [ ] **Step 7: Verify the pin, then update `terraform.yaml`**

The auth action publishes a floating `v3` tag (checked 2026-09-19 via the GitHub API); re-check before pinning:
```bash
curl -s "https://api.github.com/repos/google-github-actions/auth/tags?per_page=5" | python3 -c 'import sys,json; print([t["name"] for t in json.load(sys.stdin)])'
```
Edit `.github/workflows/terraform.yaml`:

1. Add `gcp` to the dispatch input's options:
```yaml
        options:
          - foundation
          - cluster
          - gcp
```
2. Add a third leg to the plan matrix:
```yaml
          - stack: gcp
            dir: terraform/gcp
```
3. In the `plan` job, after the `configure-aws-credentials` step, add:
```yaml
      - if: matrix.stack == 'gcp'
        uses: google-github-actions/auth@v3
        with:
          workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ vars.GCP_PLAN_SERVICE_ACCOUNT }}
```
4. In the `apply` job, after `configure-aws-credentials`, add:
```yaml
      - if: inputs.stack == 'gcp'
        uses: google-github-actions/auth@v3
        with:
          workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
          service_account: ${{ vars.GCP_APPLY_SERVICE_ACCOUNT }}
```
and replace the directory selection with:
```yaml
      - run: |
          case "${{ inputs.stack }}" in
            cluster) cd terraform/cluster ;;
            gcp)     cd terraform/gcp ;;
            *)       cd terraform ;;
          esac
          terraform init
          terraform apply -auto-approve
```
(The AWS step stays first in both jobs: the gcp root's state backend is S3.)

- [ ] **Step 8: Checkpoint (user runs), then read the first CI run**

Commit the module, both GCP roots' changes and the workflow on branch `ci-bootstrap-gcp-ci` (message `Give CI keyless GCP access via workload identity federation`), push, and open the PR. Read the three `plan` legs:
- `foundation`: the plan-lock policy update is the only proposed change.
- `cluster`: as before.
- `gcp`: must read **`No changes.`** using the impersonated read-only service account.

Failure hints:
- `Permission ... denied` on a pool or IAM-policy read: the plan service account is missing a role; add the smallest role that holds the permission (`gcloud iam roles describe <role>`) to `terraform/gcp-bootstrap`, apply it locally, re-run the check.
- `The given credential is rejected by the attribute condition` or an impersonation denial: decode the claims the runner actually presents, with a temporary step in a scratch workflow:
```bash
TOKEN=$(curl -sH "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=debug" | jq -r .value)
echo "${TOKEN}" | cut -d. -f2 | base64 -d 2>/dev/null | jq '{sub, repository_id, repository_owner_id, ref, environment}'
```
and compare `sub` against the strings in `modules/gcp_github_wif`. (Needs `permissions: id-token: write`; the token is an OIDC ID token, not a secret you are storing, but do not paste it into shared places.)
- "API not enabled": enable exactly that API, as in Step 4.

- [ ] **Step 9: Prove the apply path**

After the PR merges: Actions -> **Terraform** -> Run workflow with `stack = gcp`. Acceptance: green and `No changes.` (it applies an already-applied root). Then:
```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp-bootstrap plan
AWS_PROFILE=events-api-tf terraform -chdir=terraform/gcp plan
```
Acceptance: both `No changes.` Confirm the BigQuery sinks are unaffected (`kubectl get kafkaconnector -n events-api`, only if a cluster is up).

- [ ] **Step 10: Record it**

Add a `WHATS_NEXT.md` entry: what the first CI run surfaced, the final roles that were actually needed, and that GCP is now CI-managed except the local-only `terraform/gcp-bootstrap/`.

---

## Plan self-review (author's notes)

- **Spec coverage:** D1 (persistence split) and D4 (two roots, decoupling, alarm, one-time migration, `stack` input) are Tasks 1-5 and 7-8 (the foundation-root removal is Task 4 Step 10, the cutover is Task 7); D3's `bootstrap` role and access entry are Tasks 3 and 4; D8's matrix and `ecr_push` ARN are Tasks 3 and 6. D5-D7, `up`/`down` and acceptance are Plan 2.
- **Additions beyond the spec:** the third root `terraform/gcp/` (Task 1) and, at your request, keyless GCP access for CI with a fourth root `terraform/gcp-bootstrap/` (Task 9). The spec left GCP "as is"; verification showed committing it into the CI-managed root breaks `terraform.yaml`. It needs your sign-off.
- **Names used across tasks:** roles `events-api-github-bootstrap`, `events-api-github-deploy`; secret `events-api/debezium-replication`; topic `events-api-dbt-build-alerts`; ServiceAccounts `events-api-bootstrap-master`, `events-api-bootstrap-roles` (created in Plan 2); outputs `bootstrap_master_irsa_role_arn`, `bootstrap_roles_irsa_role_arn`, `rds_address`, `cluster_name`, `region`, `account_id`.
