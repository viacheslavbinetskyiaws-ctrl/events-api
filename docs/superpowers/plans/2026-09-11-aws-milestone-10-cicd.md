# AWS Milestone 10 (CI/CD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work. Read-only diagnostics (`kubectl get`/`describe`,
> `aws ... describe-*`/`list-*`, `terraform plan`, `git status`/`log`) are
> fine to run directly; anything mutating (`terraform apply`, `kubectl
> apply`, file writes, `git commit`/`push`) is the user's to run.

**Goal:** Automatic tests on every push/PR, automatic image build+push to
ECR on merge to `main`, and two manually-triggered (`workflow_dispatch`)
"apply real change" paths — an app-image deploy and a Terraform apply —
replacing every by-hand `terraform apply`/`docker push` this project has
done so far with a reviewable, auditable pipeline.

**Architecture:** Three purpose-scoped OIDC-federated IAM roles
(`ecr_push`, `terraform_apply`, `deploy`) in a new, standalone Terraform
module, federated from GitHub's own OIDC issuer — no static AWS credentials
anywhere in GitHub. Two workflows: `ci.yml` (`test` automatic →
`build-push` automatic on `main` → `deploy` manual/`workflow_dispatch`) and
`terraform.yml` (`plan` automatic on PR → `apply` manual/`workflow_dispatch`,
tagged `environment: aws-infra` purely for its deployment-history audit
trail — GitHub Environment required-reviewer approval turned out to be
unusable on this solo repo, corrected mid-implementation; see the spec's
Context section). The `deploy` role authenticates to EKS via a new access
entry mapped to a hand-written, narrowly-scoped Kubernetes
`Role`/`RoleBinding` — the same shape Milestone 8's `k8s_viewer` already
established, not a new pattern.

**Tech Stack:** Terraform (`aws_iam_openid_connect_provider`,
`aws_iam_role`, `aws_eks_access_entry`), GitHub Actions
(`actions/checkout@v7`, `astral-sh/setup-uv@v10.1.0`,
`aws-actions/configure-aws-credentials@v6`,
`aws-actions/amazon-ecr-login@v2`, `docker/setup-buildx-action@v4`,
`docker/build-push-action@v7`), Kubernetes RBAC.

**Spec:** `docs/superpowers/specs/2026-09-11-aws-milestone-10-cicd-design.md`
— this plan implements that design directly; read both. The spec's
"Real state checked" and "Decisions made this session" sections carry
context (e.g. the `viacheslavbinetskyiaws-ctrl` GitHub identity correction)
this plan doesn't repeat.

## Global Constraints

- `AWS_PROFILE=events-api-tf` for every AWS CLI call. Region
  `eu-central-1`, account `938500344309`, cluster `events-api-eks`.
- EKS/RDS/networking are confirmed continuously live (checked this
  session) — no bootstrap needed, but run `terraform plan` before every
  `apply` and read the actual diff rather than assume it matches this
  plan's expected resource counts.
- **GitHub identity**: repo owner is `viacheslavbinetskyiaws-ctrl`, remote
  URL must use the `github-aws-personal` SSH host alias
  (`git@github-aws-personal:viacheslavbinetskyiaws-ctrl/events-api.git`) —
  **not** the plain `github.com` host, which resolves to a different key/
  account and was already hit once as a real push failure this session.
- **The GitHub repo already exists and is already this repo's `origin`**,
  confirmed live this session (`git ls-remote origin` succeeded, `main` is
  up to date). Task 1 below is the only remaining local housekeeping before
  new work starts.
- Commit messages end with the attribution trailer already used on this
  repo's most recent commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011AhzcnBBibo6jB8AfvC7E6
  ```
- Don't run `git commit`/`git push` unless the user runs it themselves or
  explicitly asks this turn — every commit step below is the user's to run.
- Images are tagged by git SHA only — no `:latest` pushed by CI.
- The `deploy` job/role is scoped to exactly the `events-api` and
  `realtime` Deployments — not `streaming`'s consumer, not the `dbt`
  CronJob.

---

### Task 1: Commit the Milestone 10 spec doc

**Files:**
- Already exists, untracked: `docs/superpowers/specs/2026-09-11-aws-milestone-10-cicd-design.md`

- [ ] **Step 1: Stage and commit it**

```bash
git add docs/superpowers/specs/2026-09-11-aws-milestone-10-cicd-design.md
git commit -m "$(cat <<'EOF'
Add design spec for AWS Milestone 10 (CI/CD)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011AhzcnBBibo6jB8AfvC7E6
EOF
)"
```

- [ ] **Step 2: Push**

```bash
git push origin main
```

---

### Task 2: New Terraform module `terraform/modules/github-oidc/`

**Files:**
- Create: `terraform/modules/github-oidc/variables.tf`
- Create: `terraform/modules/github-oidc/main.tf`
- Create: `terraform/modules/github-oidc/outputs.tf`

**Interfaces:**
- Consumes: `var.name_prefix`, `var.github_owner`, `var.github_repo`,
  `var.ecr_repository_arns` (list), `var.eks_cluster_arn` — all supplied by
  Task 4's root `main.tf` wiring.
- Produces: `ecr_push_role_arn`, `terraform_apply_role_arn`,
  `deploy_role_arn` — consumed by Task 4 (root outputs, and the new
  `module.eks` access-entry variable) and Task 6 (GitHub repo Variables).

- [ ] **Step 1: Write `variables.tf`**

```hcl
variable "name_prefix" {
  type = string
}

variable "github_owner" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "ecr_repository_arns" {
  type = list(string)
}

variable "eks_cluster_arn" {
  type = string
}
```

- [ ] **Step 2: Write `main.tf`**

```hcl
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # No thumbprint_list — AWS verifies the JWKS endpoint's TLS cert
  # directly against its trusted CA store (since July 2023), same
  # reasoning already applied to EKS's own OIDC provider in
  # modules/eks/main.tf.
}

# Shared by all three roles below — every one of them is only ever
# assumable by a workflow run on this repo's main branch.
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

# --- ecr_push: builds+pushes the 4 app images on merge to main ---

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

# --- terraform_apply: runs `terraform apply` from CI, approval-gated ---
# AdministratorAccess, matching this project's own already-accepted
# precedent (terraform-events-api/root both hold cluster-admin-equivalent
# power on this single-operator account). A least-privilege Terraform
# policy covering this repo's full resource footprint is a separate,
# substantial exercise outside this milestone's scope.

resource "aws_iam_role" "terraform_apply" {
  name               = "${var.name_prefix}-terraform-apply"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

resource "aws_iam_role_policy_attachment" "terraform_apply" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# --- deploy: runs `kubectl set image` against the live cluster ---
# Deliberately zero broad AWS permissions — only enough to resolve the
# cluster's connection details for `aws eks update-kubeconfig`. Real
# authority comes entirely from the EKS access entry + hand-written
# Role/RoleBinding (modules/eks/ + k8s/overlays/aws/deploy-rbac.yaml),
# mirroring k8s_viewer's existing shape exactly.

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

- [ ] **Step 3: Write `outputs.tf`**

```hcl
output "ecr_push_role_arn" {
  value = aws_iam_role.ecr_push.arn
}

output "terraform_apply_role_arn" {
  value = aws_iam_role.terraform_apply.arn
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
```

- [ ] **Step 4: Validate syntax before wiring it in**

```bash
cd terraform
terraform fmt -check modules/github-oidc/
terraform validate
```

Expected: `terraform validate` fails at this point with something like
`Unsupported module` or unresolved references — that's expected, since the
module isn't wired into root `main.tf` yet (Task 4). `terraform fmt -check`
should pass clean; if it reports a file, run `terraform fmt
modules/github-oidc/` to fix formatting before moving on.

---

### Task 3: Small edits to `modules/ecr/` and `modules/eks/`

**Files:**
- Modify: `terraform/modules/ecr/outputs.tf`
- Modify: `terraform/modules/eks/outputs.tf`
- Modify: `terraform/modules/eks/variables.tf`
- Modify: `terraform/modules/eks/main.tf`

**Interfaces:**
- Produces: `module.ecr.repository_arns` (map), `module.eks.cluster_arn`
  (string) — both consumed by Task 4's root `main.tf`.
- Consumes (new): `var.github_deploy_role_arn` in `modules/eks/` — supplied
  by Task 4.

- [ ] **Step 1: Add `repository_arns` to `modules/ecr/outputs.tf`**

Append to the existing file (which currently has only `repository_urls`):

```hcl
output "repository_arns" {
  description = "Map of image name to its ECR repository ARN"
  value       = { for k, v in aws_ecr_repository.this : k => v.arn }
}
```

- [ ] **Step 2: Add `cluster_arn` to `modules/eks/outputs.tf`**

Append to the existing file (which currently has `cluster_name`,
`cluster_endpoint`, `cluster_certificate_authority_data`,
`oidc_provider_arn`, `oidc_provider_url`, `cluster_security_group_id`,
`kafka_connect_node_role_arn` — no `cluster_arn` yet):

```hcl
output "cluster_arn" {
  value = aws_eks_cluster.this.arn
}
```

- [ ] **Step 3: Add `github_deploy_role_arn` to `modules/eks/variables.tf`**

Append:

```hcl
variable "github_deploy_role_arn" {
  type = string
}
```

- [ ] **Step 4: Add the new access entry to `modules/eks/main.tf`**

Add directly after the existing `aws_eks_access_entry.k8s_viewer` block
(after its closing `}`), matching that block's own comment convention:

```hcl
# Same shape as k8s_viewer above — no AWS-managed policy fallback,
# permissions come entirely from k8s/overlays/aws/deploy-rbac.yaml.
resource "aws_eks_access_entry" "github_deploy" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.github_deploy_role_arn

  kubernetes_groups = ["github-actions-deployers"]
}
```

- [ ] **Step 5: Format check**

```bash
cd terraform
terraform fmt -check modules/ecr/ modules/eks/
```

---

### Task 4: Wire the new module into root `main.tf`/`outputs.tf`, apply

**Files:**
- Modify: `terraform/main.tf`
- Modify: `terraform/outputs.tf`

**Interfaces:**
- Consumes: `module.github_oidc.deploy_role_arn` (Task 2), `module.ecr.repository_arns` (Task 3), `module.eks.cluster_arn` (Task 3).

- [ ] **Step 1: Add the `github_oidc` module block to `terraform/main.tf`**

Add after the existing `module "ecr"` block:

```hcl
module "github_oidc" {
  source = "./modules/github-oidc"

  name_prefix     = "events-api-github"
  github_owner    = "viacheslavbinetskyiaws-ctrl"
  github_repo     = "events-api"
  eks_cluster_arn = module.eks.cluster_arn

  ecr_repository_arns = [
    module.ecr.repository_arns["app"],
    module.ecr.repository_arns["streaming"],
    module.ecr.repository_arns["dbt"],
    module.ecr.repository_arns["realtime"],
  ]
}
```

- [ ] **Step 2: Add `github_deploy_role_arn` to the existing `module "eks"` block**

The existing block in `terraform/main.tf` currently reads:

```hcl
module "eks" {
  source = "./modules/eks"

  name_prefix         = "events-api"
  cluster_subnet_ids  = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids     = module.networking.private_subnet_ids
  k8s_viewer_role_arn = module.iam.k8s_viewer_role_arn
}
```

Add one line:

```hcl
  github_deploy_role_arn = module.github_oidc.deploy_role_arn
```

This creates a real dependency: `module.eks` now depends on
`module.github_oidc` (which itself depends on `module.eks.cluster_arn` for
its `eks_cluster_arn` input, and `module.ecr.repository_arns`). Terraform
resolves this fine — it's not circular (the `deploy` role's IAM resources
don't need the access entry to exist first, and the access entry is a
separate resource inside `modules/eks/` that only needs the role's ARN,
not the reverse) — but expect `terraform plan`'s dependency graph to show
`module.eks` and `module.github_oidc` each partially depending on the
other's outputs. If `terraform plan` reports a genuine cycle error instead,
stop and re-read this task rather than force it — that would mean this
plan's dependency assumption was wrong, not something to work around.

- [ ] **Step 3: Add three new outputs to `terraform/outputs.tf`**

```hcl
output "github_ecr_push_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes to push images to ECR"
  value       = module.github_oidc.ecr_push_role_arn
}

output "github_terraform_apply_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes to run terraform apply"
  value       = module.github_oidc.terraform_apply_role_arn
}

output "github_deploy_role_arn" {
  description = "OIDC-federated role GitHub Actions assumes to deploy to EKS"
  value       = module.github_oidc.deploy_role_arn
}
```

- [ ] **Step 4: Plan and read the diff — don't assume it matches**

```bash
cd terraform
terraform plan
```

Expected shape (verify against the real output, don't just trust this
list): `module.github_oidc` creates ~10 new resources (1 OIDC provider, 3
roles, 2 inline policy documents' resulting policies, 3 policy
attachments — exact count depends on how Terraform reports
`aws_iam_role_policy_attachment` vs the policies themselves); `module.eks`
shows 1 new resource (`aws_eks_access_entry.github_deploy`); no resource
anywhere should show as `destroyed` or `replaced` — this is a purely
additive change. If anything shows as destroy/replace, stop and investigate
before applying.

- [ ] **Step 5: Apply**

```bash
terraform apply
```

- [ ] **Step 6: Capture the three role ARNs for Task 6**

```bash
terraform output github_ecr_push_role_arn
terraform output github_terraform_apply_role_arn
terraform output github_deploy_role_arn
```

Save these three values — Task 6 sets them as GitHub repo Variables.

---

### Task 5: Kubernetes RBAC for the `deploy` role

**Files:**
- Create: `k8s/overlays/aws/deploy-rbac.yaml`
- Modify: `k8s/overlays/aws/kustomization.yaml`

**Interfaces:**
- Consumes: the `github-actions-deployers` Kubernetes group name — must
  match Task 3 Step 4's `kubernetes_groups` value exactly, or the access
  entry and this RoleBinding never connect.

- [ ] **Step 1: Write `k8s/overlays/aws/deploy-rbac.yaml`**

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

- [ ] **Step 2: Add it to `k8s/overlays/aws/kustomization.yaml`'s `resources:` list**

The existing list currently reads:

```yaml
resources:
  - ../../base
  - service-account.yaml
  - migration-service-account.yaml
  - hpa.yaml
  - viewer-service-account.yaml
  - viewer-rbac.yaml
  - ingress.yaml
```

Add `deploy-rbac.yaml` as a new line.

- [ ] **Step 3: Dry-run render before applying**

```bash
kubectl kustomize k8s/overlays/aws | grep -A 20 "kind: Role$"
```

Confirm the new `Role`/`RoleBinding` render with the exact `resourceNames`/
group values above — a typo here (e.g. a mismatched group name) would fail
silently: the access entry and RBAC binding just never connect, no error
anywhere.

- [ ] **Step 4: Apply**

```bash
kubectl apply -k k8s/overlays/aws
```

- [ ] **Step 5: Verify the RBAC binding actually works, as the deploy identity**

This project has no way to literally assume the `deploy` role locally, but
`kubectl auth can-i` supports impersonating a Kubernetes group directly,
which is exactly the layer this RBAC config lives at:

```bash
kubectl auth can-i patch deployment/events-api -n events-api --as-group=github-actions-deployers --as=irrelevant-user
kubectl auth can-i patch deployment/realtime -n events-api --as-group=github-actions-deployers --as=irrelevant-user
kubectl auth can-i patch deployment/postgres -n events-api --as-group=github-actions-deployers --as=irrelevant-user
kubectl auth can-i delete deployment/events-api -n events-api --as-group=github-actions-deployers --as=irrelevant-user
```

Expected: `yes`, `yes`, `no`, `no` — confirms the RBAC scoping is exactly
as narrow as designed (only `patch` on the two named Deployments, nothing
else) before the real IAM role ever gets a chance to test it end-to-end in
Task 11.

---

### Task 6: GitHub Environment + repo Variables

**Files:** none (GitHub web UI / `gh` CLI, no files in this repo)

- [ ] **Step 1: Create the `aws-infra` Environment (no protection rules)**

In the `events-api` repo on GitHub: Settings → Environments → New
environment → name it `aws-infra` → leave every protection rule
unconfigured. (Required reviewers is unusable here — GitHub's
reviewer-search never returns the repo owner's own account on a solo repo,
same "can't request yourself as a reviewer" restriction as PRs. This
Environment exists purely so `terraform.yml`'s `apply` job — manually
triggered via `workflow_dispatch`, not gated by this Environment — shows up
in a real deployment-history audit trail.)

- [ ] **Step 2: Set repo Variables**

Settings → Secrets and variables → Actions → Variables tab (not Secrets —
none of these are sensitive, same reasoning this repo already applies to
hardcoding the account ID directly into `k8s/overlays/aws/*.yaml`). Add:

| Name | Value |
|---|---|
| `AWS_ECR_PUSH_ROLE_ARN` | (Task 4 Step 6 output) |
| `AWS_DEPLOY_ROLE_ARN` | (Task 4 Step 6 output) |
| `AWS_TERRAFORM_APPLY_ROLE_ARN` | (Task 4 Step 6 output) |
| `AWS_REGION` | `eu-central-1` |
| `EKS_CLUSTER_NAME` | `events-api-eks` |
| `ECR_REGISTRY` | `938500344309.dkr.ecr.eu-central-1.amazonaws.com` |

---

### Task 7: `.github/workflows/ci.yaml`

**Files:**
- Create: `.github/workflows/ci.yaml`

- [ ] **Step 1: Write the file**

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

- [ ] **Step 2: Validate YAML syntax locally before pushing**

```bash
uv run --with pyyaml python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml'))" && echo "valid YAML"
```

---

### Task 8: `.github/workflows/terraform.yaml`

**Files:**
- Create: `.github/workflows/terraform.yaml`

- [ ] **Step 1: Write the file**

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

`apply` only runs when manually triggered — `environment: aws-infra` here
is for the deployment-history audit trail only (Task 6 created it with no
protection rules), not a gate.

- [ ] **Step 2: Validate YAML syntax locally before pushing**

```bash
uv run --with pyyaml python3 -c "import yaml; yaml.safe_load(open('.github/workflows/terraform.yaml'))" && echo "valid YAML"
```

---

### Task 9: Commit and push everything, open a verification PR

**Files:** none new — commits Tasks 2-8's files together.

- [ ] **Step 1: Review the full diff before staging**

```bash
git status
git diff
```

- [ ] **Step 2: Stage and commit**

```bash
git add terraform/modules/github-oidc/ terraform/modules/ecr/outputs.tf \
  terraform/modules/eks/outputs.tf terraform/modules/eks/variables.tf \
  terraform/modules/eks/main.tf terraform/main.tf terraform/outputs.tf \
  k8s/overlays/aws/deploy-rbac.yaml k8s/overlays/aws/kustomization.yaml \
  .github/workflows/ci.yaml .github/workflows/terraform.yaml

git commit -m "$(cat <<'EOF'
Milestone 10: CI/CD via GitHub Actions, OIDC-federated (no static AWS creds)

New terraform/modules/github-oidc/: three purpose-scoped IAM roles
(ecr_push, terraform_apply, deploy), federated from GitHub's own OIDC
issuer. ci.yml runs tests automatically on every push/PR, builds and
pushes all 4 images to ECR automatically on merge to main (SHA-tagged),
and offers a manual workflow_dispatch deploy job scoped via a new EKS
access entry + Kubernetes RBAC Role (mirrors Milestone 8's k8s_viewer
pattern) to exactly the events-api/realtime Deployments. terraform.yml
runs `plan` automatically on any PR touching terraform/, `apply` triggered
manually via workflow_dispatch (tagged with the aws-infra Environment
purely for its deployment-history audit trail, not as a gate — required
reviewers turned out to be unusable on this solo repo).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011AhzcnBBibo6jB8AfvC7E6
EOF
)"
```

- [ ] **Step 3: Push directly to `main` this once**

This is the one time pushing straight to `main` is correct — the workflows
themselves don't exist in GitHub's eyes until they're on `main` (or at
least on the branch a PR targets), so there's no "test on a PR first"
option for their own initial creation.

```bash
git push origin main
```

- [ ] **Step 4: Confirm `build-push` fires automatically and succeeds**

Watch the Actions tab in GitHub, or:

```bash
gh run watch --repo viacheslavbinetskyiaws-ctrl/events-api
```

(If `gh` isn't installed: `brew install gh && gh auth login` first — or
just watch the Actions tab in the browser.)

- [ ] **Step 5: Confirm all 4 images landed in ECR with this commit's SHA**

```bash
COMMIT_SHA=$(git rev-parse HEAD)
for repo in app streaming dbt realtime; do
  aws ecr describe-images --repository-name events-api-$repo \
    --image-ids imageTag=$COMMIT_SHA --profile events-api-tf --region eu-central-1 \
    --query 'imageDetails[0].{repo:repositoryName,tag:imageTags[0],pushedAt:imagePushedAt}'
done
```

Expected: all 4 calls return a real image entry, no `ImageNotFoundException`.

---

### Task 10: Verify the `test` job runs automatically on a PR

**Files:** none — a throwaway branch/change to exercise the pipeline.

- [ ] **Step 1: Create a trivial feature branch**

```bash
git checkout -b verify-ci-test-job
```

- [ ] **Step 2: Make a trivial, real change** (something `pytest` actually
  exercises, not a no-op — e.g. a one-line comment in a file a unit test
  imports, or add a genuinely new trivial unit test)

- [ ] **Step 3: Commit and push the branch**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Verify CI test job runs automatically on a PR

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011AhzcnBBibo6jB8AfvC7E6
EOF
)"
git push -u origin verify-ci-test-job
```

- [ ] **Step 4: Open a PR against `main`**

```bash
gh pr create --title "Verify CI test job" --body "Verification PR for Milestone 10 — confirms test job runs automatically."
```

(Or via the GitHub web UI if `gh` isn't installed — GitHub prints a
"Compare & pull request" link right after the push either way.)

- [ ] **Step 5: Confirm in the Actions tab**: the `test` job (and only
  `test` — `build-push` should show as skipped, since this isn't `main`)
  ran automatically with no manual trigger, and passed.

- [ ] **Step 6: Merge the PR**

```bash
gh pr merge --squash
```

- [ ] **Step 7: Confirm `build-push` now runs on `main`**, producing 4 new
  images tagged with the merge commit's SHA — same verification query as
  Task 9 Step 5, against the new SHA.

---

### Task 11: Verify the manual `deploy` job

**Files:** none.

- [ ] **Step 1: Note the current running image tag, before deploying**

```bash
kubectl get deployment events-api -n events-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
```

- [ ] **Step 2: Trigger the deploy job with the SHA from Task 10 Step 7**

```bash
gh workflow run ci.yml -f image_tag=<sha-from-task-10>
```

- [ ] **Step 3: Watch it run**

```bash
gh run watch
```

- [ ] **Step 4: Confirm the Deployment's image actually changed**

```bash
kubectl get deployment events-api -n events-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl get deployment realtime -n events-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
```

Expected: both now reference the SHA tag just deployed, not `:latest` or
the prior tag. Also confirm new pods actually came up:

```bash
kubectl get pods -n events-api -l app=events-api
kubectl get pods -n events-api -l app=realtime
```

Expected: `Running`/`Ready`, recent `AGE` matching when the deploy job ran.

---

### Task 12: Verify the Terraform plan/apply flow

**Files:**
- Modify (trivially, throwaway): one `description` field in any
  `terraform/**` file, e.g. an existing `output` block's `description`.

- [ ] **Step 1: Create a branch, make a trivial low-risk change**

```bash
git checkout main
git pull
git checkout -b verify-terraform-cicd
```

Edit any existing output's `description` string in `terraform/outputs.tf`
— low-risk (a plan-only, no-resource-change diff), real enough to exercise
the pipeline honestly.

- [ ] **Step 2: Commit, push, open a PR**

```bash
git add terraform/outputs.tf
git commit -m "$(cat <<'EOF'
Verify terraform.yml plan/apply pipeline

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011AhzcnBBibo6jB8AfvC7E6
EOF
)"
git push -u origin verify-terraform-cicd
gh pr create --title "Verify terraform CI/CD" --body "Trivial description-only change to verify the plan/apply flow."
```

- [ ] **Step 3: Confirm `plan` runs automatically**, and its output (a
  no-resource-change plan, purely the description diff) is visible in the
  job log.

- [ ] **Step 4: Merge the PR**

```bash
gh pr merge --squash
```

- [ ] **Step 5: Confirm `apply` does NOT run automatically** — merging
  alone should trigger nothing; `terraform.yml` only reacts to `pull_request`
  (for `plan`) and `workflow_dispatch` (for `apply`), so the Actions tab
  should show no new `apply` run yet.

- [ ] **Step 6: Manually trigger `apply`**

```bash
git checkout main
git pull
gh workflow run terraform.yml
```

- [ ] **Step 7: Confirm `apply` runs and succeeds**, and that the run
  appears under Settings → Environments → `aws-infra`'s deployment history
  — confirming the Environment's audit-trail purpose works even without any
  protection rules attached to it.

---

### Task 13: Document real outcomes in `WHATS_NEXT.md`

**Files:**
- Modify: `WHATS_NEXT.md`

- [ ] **Step 1: Add a new milestone entry**, following this project's
  existing convention exactly — real bugs found (if any occurred during
  Tasks 4-12 that this plan didn't anticipate), what was verified live and
  how, matching the density/style of the existing `AWS_PLAN.md Milestone 9`
  entry. Template shape to follow (fill in with what actually happened, not
  this plan's predictions):

```markdown
- **`AWS_PLAN.md` Milestone 10 (CI/CD): done and verified end-to-end** —
  [one-line summary of what actually took longer/differed from scoping, if
  anything did].
  - [New OIDC-federated IAM roles, what they're scoped to.]
  - [Any real bugs hit during Tasks 4-12 — trust policy issues, RBAC
    mismatches, workflow YAML errors — exactly as encountered, not
    predicted.]
  - Verified live: [test job ran automatically on PR #___, build-push
    produced 4 images tagged ___, deploy job changed the running
    Deployment's image from ___ to ___, terraform apply ran cleanly via
    manual workflow_dispatch and appeared in the aws-infra Environment's
    deployment history].
```

- [ ] **Step 2: Commit and push**

```bash
git add WHATS_NEXT.md
git commit -m "$(cat <<'EOF'
Document Milestone 10 (CI/CD) outcomes in WHATS_NEXT.md

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011AhzcnBBibo6jB8AfvC7E6
EOF
)"
git push origin main
```

---

## Self-Review Notes

- **Spec coverage**: every numbered Design section (1-8) in the spec maps
  to a task above — module creation (Task 2), ecr/eks wiring (Task 3),
  root wiring + apply (Task 4), RBAC (Task 5), GitHub setup (Task 6), both
  workflow files (Tasks 7-8), plus the spec's Verification section's 5
  numbered checks map onto Tasks 9-12 directly (test-on-PR, build-push,
  deploy, terraform plan/apply-gate).
- **Type/name consistency checked**: `github-actions-deployers` (the
  Kubernetes group name) appears identically in Task 3 Step 4
  (`kubernetes_groups`) and Task 5 Step 1 (`RoleBinding` subject) — a
  mismatch here would silently break the RBAC binding with no error
  anywhere, so this is the one place in this plan where a literal string
  must match exactly across two separate files.
- **Explicitly not re-litigated here**: the spec's "Explicitly out of
  scope" section (GitOps/ArgoCD, a least-privilege Terraform policy,
  Ansible, `IMMUTABLE` ECR tags, a Terraform GitHub provider) — none of it
  gets a task in this plan, on purpose.
