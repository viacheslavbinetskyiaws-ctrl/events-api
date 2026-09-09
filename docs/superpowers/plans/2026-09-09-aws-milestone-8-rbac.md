# AWS Milestone 8 (RBAC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work. Read-only diagnostics (`kubectl get`/`describe`,
> `aws ... describe-*`/`list-*`, `terraform plan`) are fine to run directly;
> anything mutating (`terraform apply`, `kubectl apply`, file edits) is the
> user's to run.

**Goal:** A real, hand-written Kubernetes `Role`/`RoleBinding` for a genuinely
motivated case, proven two ways — via a new least-privilege IAM identity
mapped through an EKS access entry's `kubernetes_groups`, and via a pod
running as a ServiceAccount — without touching either of the two existing
cluster-admin identities' live access.

**Architecture:** A new IAM role (`events-api-k8s-viewer`) with zero attached
AWS permissions, given its own EKS access entry using `kubernetes_groups`
(not an access policy). One namespaced `Role` (read-only: pods/pods-log/
deployments, no secrets, no writes) and one `RoleBinding` binding **two**
subjects — `Group: events-api-viewers` (populated by the access entry) and
`ServiceAccount: events-api-viewer` — to that same `Role`. Verified
independently both ways: assuming the new role for the Group path, and a
throwaway pod for the ServiceAccount path.

**Tech Stack:** Terraform (`aws_iam_role`, `aws_eks_access_entry` with
`kubernetes_groups`), Kubernetes RBAC (`rbac.authorization.k8s.io/v1`),
`rancher/kubectl:v1.36.2` for the throwaway verification pod — not
`bitnami/kubectl`, whose free Docker Hub catalog Broadcom restructured
starting August 28, 2025 (moved to the frozen `bitnamilegacy` repo or
capped to an unpinned `latest` tag under `bitnamisecure`). Confirmed live:
`rancher/kubectl:v1.36.2` is actively maintained, multi-arch
(`linux/amd64`+`linux/arm64`), and matches this cluster's own Kubernetes
version (`1.36`) exactly.

**Spec:** `docs/superpowers/specs/2026-09-08-aws-milestone-8-rbac-design.md`
— this plan implements that design directly; read both.

## Global Constraints

- `AWS_PROFILE=events-api-tf` for every AWS CLI call. Region `eu-central-1`,
  account `938500344309`, cluster `events-api-eks`.
- EKS and RDS are both confirmed live already (`ACTIVE`/`available`, checked
  this session) — no bootstrap needed, but run `terraform plan` before every
  `apply` and read the actual diff rather than assume it matches this plan's
  expected counts.
- Do not touch `terraform-events-api`'s or `root`'s existing
  `aws_eks_access_entry`/`aws_eks_access_policy_association` resources in
  `terraform/modules/eks/main.tf` anywhere in this plan — swapping those off
  cluster-admin is a deliberately separate, later decision (see the design
  doc's "Explicitly out of scope").
- The CDC/Mongo/realtime stack stays scaled to zero throughout — nothing in
  this milestone needs it.
- No `secrets` verb anywhere in the new `Role` — this is the one rule this
  entire milestone exists to demonstrate correctly.
- Don't run `git commit` unless explicitly asked in that turn — the final
  task hands over the command, doesn't run it.
- If `terraform plan` in Task 1 reports a dependency cycle between
  `module.iam` and `module.eks`, stop rather than improvise a fix — move the
  `aws_eks_access_entry.k8s_viewer` resource out of `modules/eks/main.tf`
  into the root `terraform/main.tf` directly (referencing
  `module.eks.cluster_name` and `module.iam.k8s_viewer_role_arn` from
  there), then re-plan. This isn't expected — the new role's trust policy
  depends on nothing from `module.eks`, so the two modules' new
  cross-references run through different resource pairs than the existing
  `module.iam` → `module.eks.oidc_provider_arn` flow — but confirm against
  the real `plan` output rather than assume it holds.

---

### Task 1: New least-privilege IAM role + EKS access entry (Terraform)

**Files:**
- Modify: `terraform/modules/iam/main.tf`
- Modify: `terraform/modules/iam/outputs.tf`
- Modify: `terraform/modules/eks/variables.tf`
- Modify: `terraform/modules/eks/main.tf`
- Modify: `terraform/main.tf`
- Modify: `terraform/outputs.tf`

**Interfaces:**
- Produces: `k8s_viewer_role_arn` (root-level Terraform output), consumed by
  Task 3's `aws eks update-kubeconfig --role-arn` verification step.
- Produces: a live EKS access entry mapping that role to Kubernetes group
  `events-api-viewers`, consumed by Task 2's `RoleBinding` subject and
  Task 3's verification.

- [ ] **Step 1: Add the new IAM role**

At the end of `terraform/modules/iam/main.tf` (after the existing
`aws_iam_role_policy_attachment.dbt_irsa_cloudwatch` block), add:

```hcl
# Zero AWS permissions attached, deliberately — same shape as
# kafka_connect_gcp_irsa above: this role's only job is proving "this is a
# legitimate AWS-authenticated caller." All real authorization for this
# identity comes from Kubernetes RBAC (see k8s/overlays/aws/viewer-rbac.yaml),
# not from anything IAM grants it.
data "aws_iam_policy_document" "k8s_viewer_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [data.aws_caller_identity.current.arn]
    }
  }
}

resource "aws_iam_role" "k8s_viewer" {
  name               = "${var.name_prefix}-k8s-viewer"
  assume_role_policy = data.aws_iam_policy_document.k8s_viewer_trust.json
}
```

- [ ] **Step 2: Output the role ARN**

At the end of `terraform/modules/iam/outputs.tf`, add:

```hcl
output "k8s_viewer_role_arn" {
  description = "IAM role for the least-privilege K8s RBAC demo — no AWS permissions attached, only trusted to authenticate to EKS via an access-entry Kubernetes group"
  value       = aws_iam_role.k8s_viewer.arn
}
```

At the end of root `terraform/outputs.tf`, add:

```hcl
output "k8s_viewer_role_arn" {
  description = "IAM role for the least-privilege K8s RBAC demo — no AWS permissions attached, only trusted to authenticate to EKS via an access-entry Kubernetes group"
  value       = module.iam.k8s_viewer_role_arn
}
```

- [ ] **Step 3: New variable on the eks module**

In `terraform/modules/eks/variables.tf`, add:

```hcl
variable "k8s_viewer_role_arn" {
  type        = string
  description = "IAM role ARN mapped into the cluster via kubernetes_groups, authorized only through hand-written K8s RBAC — no access policy attached"
}
```

- [ ] **Step 4: New access entry, groups only**

In `terraform/modules/eks/main.tf`, find this existing block:

```hcl
resource "aws_eks_access_entry" "root" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
}

resource "aws_eks_access_policy_association" "root_admin" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}
```

Immediately after it (still before the `ebs_csi_irsa_trust` data source
further down the file), add:

```hcl
# Deliberately no aws_eks_access_policy_association here — this identity's
# only path to any permission is the hand-written Role/RoleBinding in
# k8s/overlays/aws/viewer-rbac.yaml, not an AWS-managed policy fallback.
resource "aws_eks_access_entry" "k8s_viewer" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.k8s_viewer_role_arn

  kubernetes_groups = ["events-api-viewers"]
}
```

- [ ] **Step 5: Wire the new role's ARN into the eks module**

In root `terraform/main.tf`, find the `module "eks"` block:

```hcl
module "eks" {
  source = "./modules/eks"

  name_prefix        = "events-api"
  cluster_subnet_ids = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids    = module.networking.private_subnet_ids
}
```

Add the new line:

```hcl
module "eks" {
  source = "./modules/eks"

  name_prefix         = "events-api"
  cluster_subnet_ids  = concat(module.networking.subnet_ids, module.networking.private_subnet_ids)
  node_subnet_ids     = module.networking.private_subnet_ids
  k8s_viewer_role_arn = module.iam.k8s_viewer_role_arn
}
```

- [ ] **Step 6: Plan, review, apply**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform plan
```

Expect exactly 2 resources to add: `aws_iam_role.k8s_viewer` and
`aws_eks_access_entry.k8s_viewer` (the two new `data` sources don't count
toward the total). Read the actual printed count before applying rather
than assuming it matches — if it doesn't, stop and report the real diff
before proceeding. If `plan` instead reports an error about a cycle between
`module.iam` and `module.eks`, see this plan's Global Constraints fallback.

```bash
AWS_PROFILE=events-api-tf terraform apply
```

- [ ] **Step 7: Confirm the outputs**

```bash
terraform output -raw k8s_viewer_role_arn
```

Expected: a real role ARN, `arn:aws:iam::938500344309:role/events-api-iam-k8s-viewer`.
Keep this value — Task 3 needs it.

---

### Task 2: Kubernetes RBAC manifests, dry-run validated (no cluster touched yet)

**Files:**
- Create: `k8s/overlays/aws/viewer-service-account.yaml`
- Create: `k8s/overlays/aws/viewer-rbac.yaml`
- Modify: `k8s/overlays/aws/kustomization.yaml`

**Interfaces:**
- Consumes: nothing from Task 1 directly (the group name `events-api-viewers`
  is a plain string shared by both the Terraform access entry and this
  `RoleBinding` — there's no Terraform-to-Kubernetes reference here, just a
  name both sides must spell identically).
- Produces: the `Role`/`RoleBinding`/`ServiceAccount` objects Task 3 and
  Task 4 both verify against.

- [ ] **Step 1: The ServiceAccount**

Create `k8s/overlays/aws/viewer-service-account.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: events-api-viewer
  namespace: events-api
```

No `eks.amazonaws.com/role-arn` annotation — unlike `events-api-app`/
`events-api-migrate`, this identity never calls an AWS API. It only needs to
exist as a Kubernetes-native identity for Task 4's pod.

- [ ] **Step 2: The Role and RoleBinding**

Create `k8s/overlays/aws/viewer-rbac.yaml`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: events-api-viewer
  namespace: events-api
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: events-api-viewer
  namespace: events-api
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: events-api-viewer
subjects:
  - kind: Group
    name: events-api-viewers
    apiGroup: rbac.authorization.k8s.io
  - kind: ServiceAccount
    name: events-api-viewer
    namespace: events-api
```

Two separate rule blocks, not one — `pods`/`pods/log` are core-group
resources (`apiGroups: [""]`), `deployments` is in `apps`. A single block
listing `deployments` under `apiGroups: [""]` would silently match nothing.
`secrets` is deliberately absent.

- [ ] **Step 3: Wire both files into the overlay**

In `k8s/overlays/aws/kustomization.yaml`, the `resources:` list currently
reads:

```yaml
resources:
  - ../../base
  - service-account.yaml
  - migration-service-account.yaml
  - hpa.yaml
```

Add the two new files:

```yaml
resources:
  - ../../base
  - service-account.yaml
  - migration-service-account.yaml
  - hpa.yaml
  - viewer-service-account.yaml
  - viewer-rbac.yaml
```

- [ ] **Step 4: Dry-run render before touching the cluster**

```bash
kubectl kustomize k8s/overlays/aws
```

Read the actual rendered output — confirm all three new objects appear
(`ServiceAccount/events-api-viewer`, `Role/events-api-viewer`,
`RoleBinding/events-api-viewer`), each with `namespace: events-api` set,
and that the `RoleBinding`'s `subjects` list has exactly the two entries
from Step 2. This project's own history (Milestone 2's kustomize work) has
caught real bugs — a missing `metadata.namespace`, a wrong `selector` — at
exactly this dry-run stage, before they could reach the live cluster.

---

### Task 3: Apply the overlay, verify the Group/access-entry path

**Files:** none (cluster-side apply + verification only)

**Interfaces:**
- Consumes: `k8s_viewer_role_arn` from Task 1 Step 7; the rendered manifests
  from Task 2.

- [ ] **Step 1: Apply**

```bash
export AWS_PROFILE=events-api-tf
aws eks update-kubeconfig --name events-api-eks --region eu-central-1
kubectl apply -k k8s/overlays/aws
```

Expect exactly 3 new objects created (`serviceaccount/events-api-viewer`,
`role.rbac.authorization.k8s.io/events-api-viewer`,
`rolebinding.rbac.authorization.k8s.io/events-api-viewer`) — every other
line should read `unchanged`, not `configured`, confirming nothing else in
the overlay drifted.

- [ ] **Step 2: Generate a kubeconfig context for the new role**

```bash
aws eks update-kubeconfig --name events-api-eks --region eu-central-1 \
  --role-arn "$(terraform -chdir=terraform output -raw k8s_viewer_role_arn)" \
  --alias events-api-viewer --user-alias events-api-viewer
```

`--user-alias` is required here, not optional — `aws eks update-kubeconfig`
names the underlying `user` (exec-credential) entry after the cluster ARN
by default, regardless of `--alias`. Without `--user-alias`, a
`--role-arn` invocation silently overwrites the *same* shared user entry
the plain admin context also points to, clobbering your own admin
credentials in the process (confirmed live: `kubectl config view --raw`
showed both contexts pointing at one `user` entry whose `exec.args` had
been overwritten to bake in `--role`). If this already happened, restore
the admin context separately: `aws eks update-kubeconfig --name
events-api-eks --region eu-central-1` (no `--role-arn`).

Access-entry changes are eventually consistent (AWS's own docs: "may take
several seconds to be effective") — if the first `auth can-i` check below
comes back as an outright authentication error rather than a clean
allow/deny, wait ~10-15 seconds and retry before treating it as a real
failure.

- [ ] **Step 3: Run the real checks and record the actual output**

```bash
kubectl --context events-api-viewer auth can-i get pods -n events-api
kubectl --context events-api-viewer auth can-i delete pods -n events-api
kubectl --context events-api-viewer auth can-i get secrets -n events-api
kubectl --context events-api-viewer auth can-i get pods -n kube-system
```

Expected, in order: `yes`, `no`, `no`, `no` (the last one proves the `Role`
is namespaced, not cluster-wide — a `Group` with only this namespace's
`RoleBinding` gets nothing outside `events-api`). Read and note the actual
output of each command — this is what gets recorded in Task 5, not the
expected values restated as if observed.

---

### Task 4: Verify the ServiceAccount/pod path

**Files:** none (cluster-side verification only)

**Interfaces:**
- Consumes: the `events-api-viewer` ServiceAccount and `Role`/`RoleBinding`
  from Task 2, applied in Task 3 Step 1.

- [ ] **Step 1: Allowed action, from inside a real pod**

```bash
kubectl -n events-api run rbac-verify-allow --rm -i --restart=Never \
  --image=rancher/kubectl:v1.36.2 \
  --overrides='{"apiVersion":"v1","spec":{"serviceAccountName":"events-api-viewer"}}' \
  -- auth can-i get pods -n events-api
```

`kubectl run` has no `--serviceaccount` flag (confirmed live against this
client's actual `v1.36.1` help output) — the pod's ServiceAccount has to be
set via `--overrides` with a raw JSON patch instead.

Expected: `yes`, printed by the pod itself before it exits and gets
cleaned up (`--rm`).

- [ ] **Step 2: Denied action, from inside a real pod**

```bash
kubectl -n events-api run rbac-verify-deny --rm -i --restart=Never \
  --image=rancher/kubectl:v1.36.2 \
  --overrides='{"apiVersion":"v1","spec":{"serviceAccountName":"events-api-viewer"}}' \
  -- auth can-i delete deployments -n events-api
```

Expected: `no`. This is the literal bar `AWS_PLAN.md`'s own Verification
section names for this milestone: "a pod using the custom Role can perform
exactly the permitted action and is denied everything else, confirmed via
`kubectl auth can-i`." Record the actual printed output from both steps.

---

### Task 5: Document real results, commit, teardown reminder

**Files:**
- Modify: `WHATS_NEXT.md`

- [ ] **Step 1: Record the real, observed results**

Add a new entry under the Milestone 7 entry in `WHATS_NEXT.md`, following
that file's existing style (what was built, what was checked live, what
broke if anything). Write the actual `auth can-i` output observed in Task 3
Step 3 and Task 4 Steps 1-2 — not the expected values from this plan
restated as if they were the observed result. Note explicitly, per the
design doc's own honest-framing requirement: the correct claim is "denied
every write in `events-api`, and denied all `secrets` reads" — not "denied
everything," since any authenticated identity on this cluster still picks
up the built-in discovery `ClusterRoleBinding`s bound to
`system:authenticated`.

- [ ] **Step 2: Hand over the final commit**

```bash
git add terraform/modules/iam/main.tf terraform/modules/iam/outputs.tf \
  terraform/modules/eks/main.tf terraform/modules/eks/variables.tf \
  terraform/main.tf terraform/outputs.tf \
  k8s/overlays/aws/viewer-service-account.yaml \
  k8s/overlays/aws/viewer-rbac.yaml \
  k8s/overlays/aws/kustomization.yaml \
  WHATS_NEXT.md
git commit -m "$(cat <<'EOF'
Milestone 8: Kubernetes RBAC via a new least-privilege IAM role

New IAM role with zero attached AWS permissions, mapped into the cluster
via an EKS access entry's kubernetes_groups (no access policy). A
namespaced Role/RoleBinding (read-only pods/deployments, no secrets)
binds both the mapped Group and a new ServiceAccount, verified two ways
via kubectl auth can-i — proving the fix for the already-flagged
AmazonEKSClusterAdminPolicy over-privilege without touching either
existing cluster-admin identity's live access.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KZfktKJ1d5yT7Y6XQ594AW
EOF
)"
```

Per this repo's own discipline: don't run this commit unless explicitly
asked to in this turn.

- [ ] **Step 3: Teardown reminder, not part of this task's deliverable**

The new IAM role (`modules/iam`) is designed to persist across the
per-milestone EKS/networking destroy cycle, same as the existing IRSA
roles — nothing new to clean up there. The access entry, `Role`,
`RoleBinding`, and `ServiceAccount` all die naturally with the cluster
itself. Confirm with the user whether to tear down EKS/RDS/networking now
(`terraform destroy -target=module.eks -target=module.networking`) or leave
them live for the next milestone (ALB Ingress, Milestone 9), per
`AWS_PLAN.md`'s standing per-milestone teardown discipline.
