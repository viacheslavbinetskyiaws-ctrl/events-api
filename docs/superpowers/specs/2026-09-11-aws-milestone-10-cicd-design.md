# AWS Milestone 10 (CI/CD) — Design

## Context

`AWS_PLAN.md`'s Milestone 10 scope, verbatim: "GitHub Actions: run the
existing `pytest` suite on push, build and push all four Docker images (app,
streaming, dbt, plus the new Node service) to ECR on merge to main.
Deployment automation gets documented as 'how this extends to auto-deploy,'
not fully built — this project's own teardown-after-session discipline means
there's usually no live cluster to auto-deploy onto; a GitOps-style follow-on
(ArgoCD, etc.) would be the real next step in an always-on environment."

`AWS_PLAN.md`'s own Verification bullet for this milestone: "a push to a
feature branch runs tests in GitHub Actions; a merge to main results in four
new image tags actually present in ECR."

**Scope expanded during this session's brainstorming**, at the user's
explicit request — the user wants to actually apply Milestone 11's
storage-shrink Terraform change *through* this pipeline rather than by hand,
and wants automatic tests but manually-confirmed deploys, for both classes of
"deploy" that exist in this project:

1. **App deploys** (new container image → running Deployment) — the
   original plan's own reasoning for leaving this undone still holds (no
   reliably-live cluster to target, in general), but is decided manual
   rather than absent: a `workflow_dispatch`-only job, so you explicitly
   trigger "ship this build" when a cluster happens to be up, rather than
   the pipeline stopping at "image sits in ECR, do the rest by hand."
2. **Infrastructure deploys** (Terraform) — a materially different, very
   standard practice (running `terraform apply` from a laptop, which is what
   every milestone in this project has done so far, is the less mature
   pattern precisely because it lacks the review trail CI naturally
   provides). New: `terraform plan` automatic on every PR touching
   `terraform/**`, `terraform apply` triggered manually
   (`workflow_dispatch`) — matching this project's own repeated real-bug
   history on Terraform applies (wrong instance types, broken trust
   policies, capacity miscalculations) with an actual human checkpoint
   before anything mutates real infrastructure, not blind auto-apply on
   merge. **Corrected mid-implementation** (originally designed as a GitHub
   Environment required-reviewer approval gate — see the note below).

Both additions stay inside this milestone's own boundary — full
auto-deploy-on-merge (GitOps/ArgoCD) is still deferred, documented rather
than built, for the reason the original plan already gives: it needs an
always-on cluster to be worth building, which this project doesn't reliably
have.

**Real platform limitation hit during implementation, corrected on the
spot**: GitHub Environment "required reviewers" turns out to be unusable on
a solo-owned repository — the reviewer-search field in the Environment
settings UI never returns the repo owner's own account as a selectable
option. This matches GitHub's well-documented "you cannot request yourself
as a PR reviewer" restriction; the Environment reviewer picker appears to
inherit the same rule, though no GitHub doc found during this session
states it explicitly for Environments. Since this is a single-operator
repo with no second account to add, "required reviewers" has no one to
require.

Fix: `terraform.yml`'s `apply` job's actual "manually confirmed" property
now comes entirely from its trigger — `workflow_dispatch`, the same
manually-invoked shape already designed for `ci.yml`'s `deploy` job — not
from an approval gate. The `aws-infra` Environment itself is kept, but
stripped down to zero protection rules (no required reviewers, no wait
timer); the `apply` job still references `environment: aws-infra` purely
for the free deployment-history audit trail GitHub tracks per-Environment
(Settings → Environments → aws-infra shows every real `apply` run, by whom,
when) — a genuinely useful record for infrastructure changes specifically,
costing nothing to keep once the gate itself doesn't work. `plan` staying
automatic on every PR is unaffected either way; `apply` is still never
automatic-on-merge, just manually *triggered* rather than manually
*approved*.

## Real state checked before designing (not assumed)

- **Git**: no remote (`git remote -v` empty) — this milestone is this
  project's first real git remote, not an add-on to an existing one. Working
  tree clean; the 20 files `NEXT_MILESTONE_PROMPT.md` flagged as uncommitted
  were already committed as `e7e593a` before this session started (stale
  note in that file, not current state).
- **EKS `ACTIVE`, RDS `available`** — confirmed live via `describe-cluster`/
  `describe-db-instances`. All 34 pods across all 3 namespaces
  (`events-api`, `kube-system`, `monitoring`) `Running`/`Completed`, none
  crash-looping. Node `ip-10-0-11-27` sits at 99% real memory — pre-existing,
  not this milestone's concern.
- **EKS/RDS/networking have been kept continuously live since Milestone 2**,
  not torn down each session (`WHATS_NEXT.md`'s own correction, reconfirmed
  today) — the per-session-destroy discipline from Milestone 0's original
  text stopped being actual practice a long time ago.
- **ECR**: 5 repos exist (`events-api-app`, `-streaming`, `-dbt`,
  `-realtime`, `-kafka-connect`). This milestone's automated build covers
  only the 4 named in `AWS_PLAN.md`'s own scope text — `kafka-connect`'s
  custom image (Milestone 3) has no ongoing rebuild need and stays manual.
- **No `.github/` directory exists anywhere in this repo yet.**
- **`realtime/` has its own independent build**: `package.json` (`tsdown`
  build), its own 3-stage `Dockerfile` (`node:26-alpine`, distinct build
  context from the root `Dockerfile`).
- **Root `Dockerfile` has 3 real build targets**: `runtime`,
  `runtime-streaming`, `runtime-dbt` (confirmed via `grep -n "^FROM"` —
  matches `NEXT_MILESTONE_PROMPT.md`'s claim, not re-derived from memory).
- **No test in `tests/` imports the `dbt`/`streaming`/`mongodb`/`bigquery`
  extras** (`grep -rn "^import\|^from" tests/integration/*.py` shows only
  `app.*` imports) — a plain `uv sync` (base deps + `dev` group) is
  sufficient to run the full suite, unit and integration both.
- **`tests/conftest.py` already self-applies Alembic migrations against
  `events_test`** and creates dbt-owned tables directly (`daily_event_counts`)
  as a fixture — nothing extra needs wiring for integration tests to work in
  CI beyond a healthy Postgres.
- **`docker-compose.yml`'s `postgres` service already has a `healthcheck`**
  (`pg_isready`) and the `events_test`-creating init script mounted — reused
  as-is in CI (`docker compose up -d postgres`), no CI-specific Postgres
  config duplicated.
- **`terraform/modules/iam/variables.tf`'s `oidc_provider_arn`/
  `oidc_provider_url` are EKS-cluster-specific** (passed from
  `module.eks.oidc_provider_arn`/`.oidc_provider_url` in root `main.tf`) —
  confirms GitHub's OIDC federation needs its own module, not a natural fit
  inside `modules/iam/`.
- **`terraform/modules/ecr/outputs.tf` only exposes `repository_urls`**, not
  ARNs — a new `repository_arns` output is needed for the new IAM policy's
  `Resource` list.
- **`k8s/overlays/aws/deployment-patch.yaml` and
  `k8s/overlays/aws-realtime/deployment.yaml` both reference `:latest` with
  `imagePullPolicy: Always`** — confirms a running pod does *not* re-pull
  automatically when a new image is pushed to the same tag; a pod only
  re-pulls when (re)scheduled (new replica, crash restart, or an explicit
  `kubectl rollout restart`/manifest change).
- **Milestone 8's RBAC work already built the exact pattern the new
  "deploy" role needs**: `terraform/modules/eks/main.tf`'s
  `aws_eks_access_entry.k8s_viewer` — an access entry with
  `kubernetes_groups = ["events-api-viewers"]` and deliberately **no**
  `aws_eks_access_policy_association` (no AWS-managed policy fallback),
  permissions coming entirely from `k8s/overlays/aws/viewer-rbac.yaml`'s
  hand-written `Role`/`RoleBinding`. Mirrored directly below.
- **`gh` CLI is not installed locally.**
- **Real GitHub identity confirmed via `ssh -T git@github-aws-personal`**
  (the `~/.ssh/config` `Host github-aws-personal` entry the user specified
  for this project, using `~/.ssh/aws_personal`) — authenticates as
  `viacheslavbinetskyiaws-ctrl`. This is *not* the same as
  `ViacheslavBinetskyiMackiev` (an earlier, wrong guess inferred from the
  repo's commit-author noreply email — commit authorship and the SSH
  identity that actually pushes/authenticates are two separate things, and
  guessing one from the other was a real mistake caught before it landed in
  Terraform). `viacheslavbinetskyiaws-ctrl` is the correct repo owner for
  every OIDC trust-policy `sub` claim below, and `git@github-aws-personal:...`
  (not the default `github.com` host) is the remote URL this project's git
  operations must use.
- **Verified live against GitHub's current docs** (not training-data
  memory): required-reviewer Environment protection rules are available on
  the Free plan specifically for **public** repositories (matches the
  already-decided public-repo choice) — private repos need Pro/Team/
  Enterprise for this feature.
- **Verified live**: GitHub's OIDC issuer is
  `https://token.actions.githubusercontent.com`; AWS has verified the JWKS
  endpoint's TLS certificate directly (ignoring any supplied thumbprint)
  since July 2023 — matches this project's own existing precedent of
  omitting `thumbprint_list` for EKS's own OIDC provider in
  `modules/eks/main.tf`, for the same underlying reason (a trusted-CA-backed
  issuer).
- **Current major versions of every GitHub Action used, verified live
  2026-09-11** (not memory): `actions/checkout@v7`, `astral-sh/setup-uv@v10.1.0`,
  `docker/setup-buildx-action@v4`, `docker/build-push-action@v7`,
  `aws-actions/configure-aws-credentials@v6`,
  `aws-actions/amazon-ecr-login@v2`.

## Decisions made this session

- **Public repo**, named **`events-api`** (matches this project's own
  internal naming convention — the EKS cluster, every ECR repo, every IAM
  role are all `events-api`-prefixed), not the local directory name
  (`k8s-tf-dbt`).
- **Both unit and integration tests run in CI** — an ephemeral,
  CI-runner-local Postgres via `docker compose up -d postgres`, never the
  real RDS instance or anything on the live cluster. Nothing in this
  workflow ever touches the live deployed stack except the two explicitly
  manual jobs (app-deploy, terraform-apply).
- **OIDC `sub` scoping: `main` branch only**, for all three new roles —
  `repo:viacheslavbinetskyiaws-ctrl/events-api:ref:refs/heads/main`. A PR
  from a fork, or any other branch, can never assume any of these roles.
  Applies even to the manually-triggered deploy job — `workflow_dispatch`
  still resolves against whichever ref you pick when triggering it, so this
  means you can only actually run the deploy job against `main`.
- **Three dedicated, purpose-scoped OIDC-federated IAM roles**, not the
  existing `terraform-events-api` admin user's static credentials and not
  one shared role:
  - `ecr_push`: `ecr:GetAuthorizationToken` (`Resource: "*"`, required — a
    token-vending action) + push-related actions
    (`BatchCheckLayerAvailability`/`InitiateLayerUpload`/`UploadLayerPart`/
    `CompleteLayerUpload`/`PutImage`/`BatchGetImage`) scoped to exactly the
    4 relevant repo ARNs.
  - `terraform_apply`: `AdministratorAccess` (AWS-managed policy) — matches
    this project's own already-accepted precedent (Milestone 2's security
    review left both `terraform-events-api` and `root` holding
    cluster-admin-equivalent power, explicitly accepted for "single-operator
    account, already holds unbounded power regardless"). A genuine
    least-privilege Terraform policy covering this project's full resource
    footprint (VPC/EKS/RDS/IAM/S3/ECR/budgets) is a separate, substantial
    exercise outside this milestone's point.
  - `deploy`: **zero AWS-managed permissions** beyond
    `eks:DescribeCluster` scoped to the one cluster ARN (needed for `aws eks
    update-kubeconfig` to resolve the endpoint/CA data) — all real authority
    comes from a new Kubernetes `Role`/`RoleBinding`, scoped to `get`/`patch`
    on exactly the `events-api` and `realtime` Deployments, nothing else.
    Mirrors `k8s_viewer`'s existing pattern exactly.
- **All three roles + the GitHub OIDC provider live in a new, standalone
  `terraform/modules/github-oidc/`** — not `modules/iam/`, since that
  module's existing resources all depend on `module.eks.oidc_provider_arn`/
  `.oidc_provider_url` (EKS's own OIDC issuer), a real coupling GitHub's
  federation has no reason to share. (Not because `modules/iam/` currently
  gets destroyed between sessions — it doesn't, in actual practice since
  Milestone 2 — but because the architectural coupling would be artificial
  either way.)
- **`ci.yml`: two jobs plus one manual job** — `test` (automatic, every push
  + PR), `build-push` (automatic, `needs: test`, only on push to `main`),
  `deploy` (manual, `workflow_dispatch` only, takes an image tag input).
  Keeps PR runs from ever touching AWS credentials at all.
- **Images tagged by git SHA only, no `:latest`.** The manual deploy job
  takes an explicit SHA input, so there's no ambiguity `:latest` would need
  to resolve — this also finally lets `modules/ecr`'s `MUTABLE` tag setting
  become unnecessary going forward, though not switched to `IMMUTABLE` in
  this milestone (unrelated change, not this milestone's point).
- **Deploy job scoped to exactly the app + realtime Deployments**, not
  `streaming`'s consumer or the `dbt` CronJob — those aren't easily
  curl-verifiable and updating them carries real consumer-offset/schedule
  risk without adding teaching value for "prove CD applies changes."
- **`terraform.yml`: `plan` automatic on PR** (any PR touching
  `terraform/**`), **`apply` triggered manually via `workflow_dispatch`,
  still tagged `environment: aws-infra` for its free deployment-history
  audit trail** — originally designed as an `aws-infra` required-reviewer
  approval gate, corrected mid-implementation once that turned out to be
  unusable on a solo-owned repo (see the Context section's note); the
  Environment itself is kept with zero protection rules, purely for the
  audit trail, not as the gate. Same manually-triggered shape as `ci.yml`'s
  `deploy` job now, for the same reason: a trigger you have to consciously
  invoke already satisfies "manually confirmed" on its own, with no
  second-party reviewer needed.
- **No Ansible** — not in the target job posting's named stack (Terraform +
  Kubernetes only), and no technical gap for it either: every place it would
  traditionally fit (server configuration, app rollout) is already owned by
  more idiomatic tools already in use here (Terraform, Kustomize/Helm).
- **GitHub-side setup (repo creation, Environment, repo variables) is manual,
  not Terraform-managed** — no GitHub Terraform provider anywhere in this
  repo, and adding one for a handful of one-time setup actions isn't worth
  the new provider/credential surface.

## Design

### 1. New Terraform module: `terraform/modules/github-oidc/`

```hcl
# variables.tf
variable "name_prefix"          { type = string }
variable "github_owner"         { type = string }
variable "github_repo"          { type = string }
variable "ecr_repository_arns"  { type = list(string) }
variable "eks_cluster_arn"      { type = string }
```

```hcl
# main.tf

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  # No thumbprint_list — AWS verifies the JWKS endpoint's TLS cert directly
  # against its trusted CA store (since July 2023), same reasoning already
  # applied to EKS's own OIDC provider in modules/eks/main.tf.
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

# --- ecr_push role ---

resource "aws_iam_role" "ecr_push" {
  name               = "${var.name_prefix}-ecr-push"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "ecr_push" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
    ]
    resources = var.ecr_repository_arns
  }
}

resource "aws_iam_policy" "ecr_push" {
  name   = "${var.name_prefix}-ecr-push-policy"
  policy = data.aws_iam_policy_document.ecr_push.json
}

resource "aws_iam_role_policy_attachment" "ecr_push" {
  role       = aws_iam_role.ecr_push.name
  policy_arn = aws_iam_policy.ecr_push.arn
}

# --- terraform_apply role ---

resource "aws_iam_role" "terraform_apply" {
  name               = "${var.name_prefix}-terraform-apply"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

resource "aws_iam_role_policy_attachment" "terraform_apply" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# --- deploy role ---
# Deliberately no broad AWS permissions — only enough to resolve the
# cluster's connection details. Real authority comes entirely from the
# EKS access entry + hand-written Role/RoleBinding (see modules/eks/,
# k8s/overlays/aws/deploy-rbac.yaml) — mirrors k8s_viewer's shape exactly.

resource "aws_iam_role" "deploy" {
  name               = "${var.name_prefix}-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

data "aws_iam_policy_document" "deploy_describe_cluster" {
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [var.eks_cluster_arn]
  }
}

resource "aws_iam_policy" "deploy_describe_cluster" {
  name   = "${var.name_prefix}-deploy-describe-cluster"
  policy = data.aws_iam_policy_document.deploy_describe_cluster.json
}

resource "aws_iam_role_policy_attachment" "deploy_describe_cluster" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy_describe_cluster.arn
}
```

```hcl
# outputs.tf
output "ecr_push_role_arn"       { value = aws_iam_role.ecr_push.arn }
output "terraform_apply_role_arn" { value = aws_iam_role.terraform_apply.arn }
output "deploy_role_arn"         { value = aws_iam_role.deploy.arn }
```

### 2. `terraform/modules/ecr/outputs.tf` — add `repository_arns`

```hcl
output "repository_arns" {
  description = "Map of image name to its ECR repository ARN"
  value       = { for k, v in aws_ecr_repository.this : k => v.arn }
}
```

### 3. `terraform/modules/eks/` — new access entry for the `deploy` role

New variable in `variables.tf`:

```hcl
variable "github_deploy_role_arn" {
  type = string
}
```

New resource in `main.tf`, directly after the existing `k8s_viewer` access
entry (same "no `aws_eks_access_policy_association`, permissions live
entirely in the hand-written RBAC file" comment convention):

```hcl
# Same shape as k8s_viewer above — no AWS-managed policy fallback,
# permissions come entirely from k8s/overlays/aws/deploy-rbac.yaml.
resource "aws_eks_access_entry" "github_deploy" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.github_deploy_role_arn

  kubernetes_groups = ["github-actions-deployers"]
}
```

### 4. Root `terraform/main.tf` / `outputs.tf`

New module block:

```hcl
module "github_oidc" {
  source = "./modules/github-oidc"

  name_prefix   = "events-api-github"
  github_owner  = "viacheslavbinetskyiaws-ctrl"
  github_repo   = "events-api"
  eks_cluster_arn = module.eks.cluster_arn

  ecr_repository_arns = [
    module.ecr.repository_arns["app"],
    module.ecr.repository_arns["streaming"],
    module.ecr.repository_arns["dbt"],
    module.ecr.repository_arns["realtime"],
  ]
}
```

`module "eks"` block gets one new argument:

```hcl
  github_deploy_role_arn = module.github_oidc.deploy_role_arn
```

New root outputs (needed to configure GitHub repo variables after `apply`):

```hcl
output "github_ecr_push_role_arn"       { value = module.github_oidc.ecr_push_role_arn }
output "github_terraform_apply_role_arn" { value = module.github_oidc.terraform_apply_role_arn }
output "github_deploy_role_arn"         { value = module.github_oidc.deploy_role_arn }
```

**Checked**: `terraform/modules/eks/outputs.tf` currently exposes
`cluster_name`/`cluster_endpoint`/`cluster_certificate_authority_data`/
`oidc_provider_arn`/`oidc_provider_url`/`cluster_security_group_id`/
`kafka_connect_node_role_arn` — no `cluster_arn` yet. New output needed:

```hcl
output "cluster_arn" {
  value = aws_eks_cluster.this.arn
}
```

### 5. `k8s/overlays/aws/deploy-rbac.yaml` — new file

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: github-actions-deployer
  namespace: events-api
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    resourceNames: ["events-api", "realtime"]
    verbs: ["get", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: github-actions-deployer
  namespace: events-api
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: github-actions-deployer
subjects:
  - kind: Group
    name: github-actions-deployers
    apiGroup: rbac.authorization.k8s.io
```

Added to `k8s/overlays/aws/kustomization.yaml`'s `resources:` list.

### 6. `.github/workflows/ci.yaml`

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:
  workflow_dispatch:
    inputs:
      image_tag:
        description: "Git SHA tag to deploy (from a prior build-push run)"
        required: true

permissions:
  contents: read
  id-token: write

jobs:
  test:
    if: github.event_name != 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10.1.0
      - run: uv sync
      - run: docker compose up -d --wait postgres
      - run: uv run pytest

  build-push:
    needs: test
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_ECR_PUSH_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr
      - uses: docker/setup-buildx-action@v4
      - uses: docker/build-push-action@v7
        with:
          context: .
          target: runtime
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/events-api-app:${{ github.sha }}
      - uses: docker/build-push-action@v7
        with:
          context: .
          target: runtime-streaming
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/events-api-streaming:${{ github.sha }}
      - uses: docker/build-push-action@v7
        with:
          context: .
          target: runtime-dbt
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/events-api-dbt:${{ github.sha }}
      - uses: docker/build-push-action@v7
        with:
          context: ./realtime
          target: runtime
          push: true
          tags: ${{ steps.ecr.outputs.registry }}/events-api-realtime:${{ github.sha }}

  deploy:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - run: aws eks update-kubeconfig --name ${{ vars.EKS_CLUSTER_NAME }} --region ${{ vars.AWS_REGION }}
      - run: |
          kubectl set image deployment/events-api \
            events-api=${{ vars.ECR_REGISTRY }}/events-api-app:${{ inputs.image_tag }} \
            -n events-api
          kubectl set image deployment/realtime \
            realtime=${{ vars.ECR_REGISTRY }}/events-api-realtime:${{ inputs.image_tag }} \
            -n events-api
```

Note the `test` job's `if:` guard — `workflow_dispatch` only ever exists to
run `deploy`, so it skips `test`/`build-push` entirely rather than trying to
run a no-op test pass first.

### 7. `.github/workflows/terraform.yaml`

```yaml
name: Terraform

on:
  pull_request:
    paths: ["terraform/**"]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  plan:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_TERRAFORM_APPLY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - run: |
          cd terraform
          terraform init
          terraform plan

  apply:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    environment: aws-infra
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_TERRAFORM_APPLY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - run: |
          cd terraform
          terraform init
          terraform apply -auto-approve
```

`apply` only ever runs when manually triggered (`workflow_dispatch`) — that
trigger is what makes it "manually confirmed," not the Environment.
`environment: aws-infra` is kept purely for GitHub's free per-Environment
deployment-history audit trail; the Environment itself is created in
GitHub's web UI with zero protection rules configured (no required
reviewers — that turned out to be unusable on a solo repo, see the Context
section), not by this workflow file.

### 8. Manual, one-time GitHub-side setup (not Terraform)

- Create the public repo `events-api` under `viacheslavbinetskyiaws-ctrl`
  (the GitHub account the user's `github-aws-personal` SSH identity
  authenticates as — confirmed live via `ssh -T git@github-aws-personal`,
  not the account inferred from commit-author email).
- Add the git remote using that SSH host alias, not the default
  `github.com` one — this project's `~/.ssh/config` already has a
  `Host github-aws-personal` entry (`HostName github.com`,
  `IdentityFile ~/.ssh/aws_personal`) set up specifically for this
  project's pushes:
  ```bash
  git remote add origin git@github-aws-personal:viacheslavbinetskyiaws-ctrl/events-api.git
  git push -u origin main
  ```
- Create the `aws-infra` Environment (Settings → Environments) with **no
  protection rules** — required reviewers isn't usable on a solo repo (see
  Context); this Environment exists purely so `apply` runs show up in its
  deployment-history audit trail.
- After the first `terraform apply` (of `module.github_oidc` + the new
  `module.eks` access entry) creates the three role ARNs, set repo
  Variables (Settings → Secrets and variables → Actions → Variables — not
  Secrets, since none of these values are sensitive, same reasoning this
  repo already applies to hardcoding the account ID directly into
  `k8s/overlays/aws/*.yaml`): `AWS_ECR_PUSH_ROLE_ARN`,
  `AWS_DEPLOY_ROLE_ARN`, `AWS_TERRAFORM_APPLY_ROLE_ARN`, `AWS_REGION`
  (`eu-central-1`), `EKS_CLUSTER_NAME` (`events-api-eks`), `ECR_REGISTRY`
  (`938500344309.dkr.ecr.eu-central-1.amazonaws.com`).

There's a real bootstrap ordering wrinkle here: `module.github_oidc`'s trust
policy references `repo:viacheslavbinetskyiaws-ctrl/events-api:...`, so the
repo needs to exist (with that exact name, under that exact account) before
the first `terraform apply` — but the workflow files that *use* the
resulting role ARNs need to already be in the repo before any of this can be
tested. Order: create the empty repo first → add the `github-aws-personal`
remote and push the code (workflows included, referencing repo Variables
that don't exist yet — harmless, they just won't resolve until set) → run
`terraform apply` locally one final time (by hand, the last manual apply
this project will ever need for this specific stack) → set the repo
Variables from its outputs → from here on, `terraform.yml`/`ci.yml` take
over.

## Verification (user-run)

1. **Test job, automatic**: open a PR from a feature branch touching
   anything — confirm the `test` job runs and passes in the Actions tab
   without any manual trigger.
2. **Build-push job, automatic on merge**: merge that PR to `main` — confirm
   `build-push` runs automatically and `aws ecr describe-images` shows all 4
   repos with a new image tagged with that merge commit's SHA.
3. **Deploy job, manual**: trigger `ci.yml`'s `workflow_dispatch` from the
   Actions UI (or `gh workflow run ci.yml -f image_tag=<sha>` once `gh` is
   installed) with the SHA from step 2 — confirm via `kubectl get deployment
   events-api -n events-api -o jsonpath='{.spec.template.spec.containers[0].image}'`
   that the running Deployment's image reference actually changed, and that
   a new pod comes up `Ready` with that exact tag.
4. **Terraform plan, automatic on PR**: open a PR with a trivial,
   low-risk `terraform/` change (e.g. a comment or an output description) —
   confirm `plan` runs automatically and its output is visible in the job
   log.
5. **Terraform apply, manual**: merge that PR, then manually trigger
   `terraform.yml`'s `workflow_dispatch` — confirm `apply` only ever runs
   when explicitly triggered this way (never automatically on the merge
   itself), and that the run shows up under Settings → Environments →
   `aws-infra`'s deployment history afterward.

This is `AWS_PLAN.md`'s own Milestone 10 verification bar (steps 1-2) plus
the two additions this session's scope expansion introduced (steps 3-5).

## Explicitly out of scope

- **Full auto-deploy-on-merge / GitOps (ArgoCD, Flux)** — still deferred,
  documented as the real next step for an always-on environment, per the
  original plan's own reasoning. Not built here even though EKS/RDS happen
  to be continuously live right now — an always-on *cluster* doesn't by
  itself justify building a continuous-reconciliation *controller* just to
  prove it once; that's a different, bigger piece of work.
- **A least-privilege Terraform IAM policy** (vs. reusing
  `AdministratorAccess`) — a separate, substantial exercise; not this
  milestone's point on a single-operator account that already accepts this
  exact trade-off elsewhere (`terraform-events-api`, `root`).
- **Deploying `streaming`'s consumer or the `dbt` CronJob's image** via the
  manual deploy job — scoped out, see Decisions above.
- **Ansible** — no gap for it in this project's architecture or the target
  job posting's stack.
- **Switching ECR repos to `IMMUTABLE` tag mutability** — unblocked by
  SHA-only tagging, but not actually flipped in this milestone (unrelated
  change).
- **A Terraform provider for GitHub** — repo/Environment/variable setup
  stays manual, one-time, hand-run.

## Verification bar

From `AWS_PLAN.md`'s own Verification section: "Milestone 10: a push to a
feature branch runs tests in GitHub Actions; a merge to main results in four
new image tags actually present in ECR." — extended this session (see
Verification above) to also cover the manual app-deploy and
plan-automatic/apply-manual Terraform flow.
