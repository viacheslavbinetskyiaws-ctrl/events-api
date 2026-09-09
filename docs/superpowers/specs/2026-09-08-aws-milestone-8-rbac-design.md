# AWS Milestone 8 (RBAC) — Design

## Context

`AWS_PLAN.md`'s Milestone 8 scope, verbatim: "At least one hand-written
`Role`/`RoleBinding` for a real, motivated case — the same least-privilege
instinct already applied at the Postgres-role and IAM layers, now applied to
the Kubernetes API itself." Deliberately terse — `NEXT_MILESTONE_PROMPT.md`
named the actual case as something to decide this session, not assume from
the plan's one-line text.

EKS and RDS are both still live from the Milestone 7 session (confirmed live
this session via `describe-cluster`/`describe-db-instances` — `ACTIVE`/
`available`, not assumed from a prior session's word).

## Real state checked before designing (not assumed)

- **No hand-written RBAC exists anywhere in this project.** `kubectl get
  rolebindings,clusterrolebindings -A` shows zero bindings to any of this
  project's own ServiceAccounts (`events-api-app`, `events-api-migrate`,
  `events-api-dbt`). One binding has appeared since the last session
  (`events-kafka-role` in the `events-api` namespace, 3 days old) —
  inspected directly (`kubectl get rolebinding events-kafka-role -o yaml`)
  and confirmed Strimzi-owned (`app.kubernetes.io/managed-by:
  strimzi-cluster-operator`, an `ownerReference` to the `Kafka` CR), not
  something this project wrote.
- **Nothing in this project's own code calls the Kubernetes API.** Re-grepped
  `app/`, `streaming/`, `realtime/`, `dbt/` for any Kubernetes client
  library import — none found.
- **The real, already-flagged over-privilege still stands, confirmed live**:
  `aws eks list-associated-access-policies` shows both `terraform-events-api`
  (the IAM user Terraform applies as) and the account's `root` principal
  hold `AmazonEKSClusterAdminPolicy` at `cluster` scope — full cluster-admin,
  via `aws_eks_access_entry`/`aws_eks_access_policy_association` in
  `terraform/modules/eks/main.tf`. Flagged by Milestone 2's own automated
  security review and accepted as-is at the time ("single-operator account,
  root already holds unbounded account power regardless").
- `kubectl -n events-api get serviceaccounts` confirms the project's own
  ServiceAccounts today: `events-api-app`, `events-api-dbt`,
  `events-api-migrate` — none has any RBAC binding.

## EKS access-entry mechanics, verified against AWS's own current docs (not memory)

Fetched directly from `docs.aws.amazon.com/eks/latest/userguide/` (access
entry creation and Kubernetes-groups pages) and the `terraform-provider-aws`
source, not recalled from training data:

- Each IAM principal gets exactly one EKS access entry; its `type` (default
  `STANDARD` if unset) is fixed at creation and can't change.
- A `STANDARD` access entry can carry `kubernetesGroups`, an EKS-managed
  **access policy**, or **both** — they are not mutually exclusive. This
  project's two existing entries (`terraform-events-api`, `root`) use the
  access-policy path only; nothing here has used the groups path before.
- AWS never validates that a group name set on an access entry actually
  matches an RBAC binding on the cluster — a typo silently grants nothing,
  the same class of silent failure as `accesModes`/`DbtBuildPasssed` already
  found elsewhere in this project's history.
- Confirmed against the AWS provider's own resource docs: `aws_eks_access_entry`'s
  Kubernetes-groups argument is `kubernetes_groups` — a top-level list
  argument, not nested in a block.

## Decision: which "real, motivated case," and why

Three shapes were weighed, not assumed going in:

- **(Chosen) A new, dedicated least-privilege IAM role**, scoped into the
  cluster via an access entry's `kubernetes_groups`, authorized only through
  a hand-written `Role`/`RoleBinding` — no EKS access policy attached at
  all. Directly demonstrates the fix for the already-flagged
  `AmazonEKSClusterAdminPolicy` over-privilege, without touching either
  existing identity's actual working access — zero risk of losing `kubectl`
  access to the cluster mid-session.
- **Rejected: inventing a new in-cluster operational tool** that needs K8s
  API access, scoped via a `ServiceAccount`. Nothing in this project has a
  genuine, motivated need for this today, and the most obvious candidate
  (cleaning up completed Jobs) is already natively solved by Kubernetes'
  own `ttlSecondsAfterFinished` — building a tool to replace built-in
  functionality would be the opposite of this project's own "don't build
  what's already covered" discipline.
- **Rejected (for now), kept as a named follow-on: actually swapping
  `terraform-events-api`/`root` off `AmazonEKSClusterAdminPolicy`** onto a
  scoped `kubernetes_groups` entry. This is the most direct fix for the
  flagged finding, but a real, avoidable risk of self-lockout from the
  cluster mid-milestone if the hand-written `Role` misses something needed
  later in the session. Approach A proves the exact same mechanism safely
  first; this is the natural next step once it's proven.

**Correction found during design review**: `AWS_PLAN.md`'s own Verification
line for this milestone reads "a pod using the custom Role can perform
exactly the permitted action" — a `Group` subject (populated by the access
entry) can never be a pod; a pod's identity is a `ServiceAccount`. Rather
than switching to the rejected ServiceAccount-only shape, the fix is one
`RoleBinding` with **two subjects** — `Group: events-api-viewers` and
`ServiceAccount: events-api-viewer` — verified independently against the
same `Role`. This satisfies both `AWS_PLAN.md`'s literal wording and
`NEXT_MILESTONE_PROMPT.md`'s access-entry framing from a single design,
and it happens to demonstrate the exact Group-vs-ServiceAccount subject
distinction this milestone is teaching.

## Design

### 1. New IAM role, zero attached AWS permissions (`terraform/modules/iam/`)

`aws_iam_role.k8s_viewer` (`${var.name_prefix}-k8s-viewer`), trust policy
allowing `sts:AssumeRole` from `data.aws_caller_identity.current.arn` (the
identity Terraform itself runs as — `terraform-events-api` in this
account; already has `AdministratorAccess`, which implicitly covers
`sts:AssumeRole`, so no separate IAM change is needed on that side). No
policy attachment of any kind.

This mirrors an existing pattern in this same file rather than inventing a
new one: Milestone 5's `kafka_connect_gcp_irsa` role also carries zero AWS
permissions, its only job being to prove "this is a legitimate
AWS-authenticated caller" to something else that does the real
authorization (there, GCP WIF; here, Kubernetes RBAC). New output
`k8s_viewer_role_arn`.

### 2. New EKS access entry, groups only (`terraform/modules/eks/`)

New variable `k8s_viewer_role_arn` (wired from `module.iam.k8s_viewer_role_arn`
in root `main.tf`'s `module "eks"` block — this is a one-directional
dependency on a *different* resource pair than the existing
`module.iam` → `module.eks.oidc_provider_arn` flow, so it introduces no
module cycle; the new role's trust policy needs nothing from `module.eks`
at all).

`aws_eks_access_entry.k8s_viewer`: `cluster_name = aws_eks_cluster.this.name`,
`principal_arn = var.k8s_viewer_role_arn`, `kubernetes_groups =
["events-api-viewers"]`. Deliberately **no** matching
`aws_eks_access_policy_association` — this identity's only path to any
permission is the hand-written Kubernetes `Role` below, not an
AWS-managed fallback.

A real, useful side effect of this shape: because the role's trust policy
depends on nothing from `module.eks`, it needs no update across this
project's per-milestone EKS destroy/recreate cycle — unlike the existing
IRSA roles, whose trust conditions reference the OIDC provider URL and do
get updated in place on every cluster recreation.

### 3. Kubernetes RBAC objects (`k8s/overlays/aws/`)

New `viewer-service-account.yaml` — `ServiceAccount events-api-viewer` in
`events-api`, no `eks.amazonaws.com/role-arn` annotation (unlike
`events-api-app`/`events-api-migrate`, this identity never calls an AWS
API — it only needs to exist as a Kubernetes-native identity for the
in-pod verification step below).

New `viewer-rbac.yaml` — one `Role` and one `RoleBinding`, both namespaced
to `events-api`:

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
listing `deployments` under `apiGroups: [""]` would silently grant nothing
(Kubernetes has no concept of an invalid `apiGroups`/`resources` pairing
erroring — it just never matches), the same silent-failure shape as the
access entry's unvalidated group name above.

`secrets` is deliberately absent from the rules — the sharpest parallel to
`events_app`'s own per-table Postgres grants (Milestone 7's RLS work): a
"viewer" role that can read `secrets` is the canonical real-world RBAC
mistake this design exists to avoid.

`kustomization.yaml` gains both new files under `resources:`.

## Verification

Both subjects, against the same `Role`, run separately:

**1. The `Group` / access-entry path** (proves IAM → access entry →
Kubernetes RBAC, end to end):

```bash
aws eks update-kubeconfig --name events-api-eks --region eu-central-1 \
  --role-arn <k8s_viewer_role_arn> --alias events-api-viewer --profile events-api-tf

kubectl --context events-api-viewer auth can-i get pods -n events-api        # expect: yes
kubectl --context events-api-viewer auth can-i delete pods -n events-api    # expect: no
kubectl --context events-api-viewer auth can-i get secrets -n events-api    # expect: no
kubectl --context events-api-viewer auth can-i get pods -n kube-system      # expect: no (namespaced Role, not cluster-wide)
```

**2. The `ServiceAccount` / pod path** (proves the literal bar in
`AWS_PLAN.md`'s Verification section — "a pod using the custom Role"):

```bash
kubectl -n events-api run rbac-verify --rm -i --restart=Never \
  --image=rancher/kubectl:v1.36.2 \
  --overrides='{"apiVersion":"v1","spec":{"serviceAccountName":"events-api-viewer"}}' \
  -- auth can-i get pods -n events-api        # expect: yes

kubectl -n events-api run rbac-verify --rm -i --restart=Never \
  --image=rancher/kubectl:v1.36.2 \
  --overrides='{"apiVersion":"v1","spec":{"serviceAccountName":"events-api-viewer"}}' \
  -- auth can-i delete deployments -n events-api   # expect: no
```

`kubectl run` has no `--serviceaccount` flag (confirmed live against this
client's actual `v1.36.1` help output, not assumed) — setting the pod's
ServiceAccount this way needs `--overrides` with a raw JSON patch instead.

`rancher/kubectl:v1.36.2`, not `bitnami/kubectl` — Broadcom restructured
Bitnami's free Docker Hub catalog starting August 28, 2025 (old images moved
to the frozen, unmaintained `bitnamilegacy` repo; the free replacement,
`bitnamisecure`, is capped to a `latest` tag with no version pinning).
Confirmed live via `docker buildx imagetools inspect`: `rancher/kubectl`
publishes real, actively-maintained, version-pinned multi-arch
(`linux/amd64`+`linux/arm64`) images — `v1.36.2` matches this cluster's
own Kubernetes version exactly (`aws eks describe-cluster` →
`cluster.version = "1.36"`), avoiding client/server version skew as a
side benefit of dodging the licensing question entirely.

**Honest framing for the writeup**: the correct claim is "denied every
write in `events-api`, and denied all `secrets` reads" — not "denied
everything." Any authenticated identity on this cluster already picks up
the built-in discovery bindings visible in this cluster's own
`clusterrolebindings` (`system:basic-user`, `system:discovery`,
`system:public-info-viewer`, all bound to `system:authenticated`), which
this design doesn't and can't remove.

## Teardown

- The IAM role (`modules/iam`) stays live across the per-milestone
  `terraform destroy -target=module.eks -target=module.networking` cycle,
  same as the existing IRSA roles — it costs $0 and `modules/iam` isn't
  part of that targeted destroy.
- The access entry (`modules/eks`), the `Role`, `RoleBinding`, and
  `ServiceAccount` all die naturally with the cluster/namespace itself.

## Explicitly out of scope

- Actually moving `terraform-events-api`/`root` off `AmazonEKSClusterAdminPolicy`
  (the rejected-for-now approach above) — a real next step once this
  pattern is proven, not part of this milestone's own bar.
- Any new in-cluster operational tool or workload that would need K8s API
  access for its own purposes — nothing in this project has a genuine need
  for one yet.
- A permissions boundary or SCP-level guardrail on the new IAM role —
  matches this project's existing single-operator-account posture
  (`terraform-events-api` already carries unscoped `AdministratorAccess`).

## Verification bar

From `AWS_PLAN.md`'s own Verification section: "Milestone 8: a pod using
the custom Role can perform exactly the permitted action and is denied
everything else, confirmed via `kubectl auth can-i`." Met by verification
step 2 above (the `ServiceAccount`/pod path), with step 1 (the `Group`/IAM
path) as the additional proof this session's own scoping decided was the
actual motivated case worth building.
