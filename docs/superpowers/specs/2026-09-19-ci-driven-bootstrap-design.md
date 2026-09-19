# CI-Driven Bootstrap (Cluster Up/Down) — Design

## Context

The goal, in the user's own words from the session that scoped this
(`NEXT_MILESTONE_PROMPT.md`): "I want CI/CD to update or rebuild when
something changes. And if I stop everything, I want CI/CD to recreate
everything later," with "no or minimum manual local commands." Two distinct
capabilities:

1. **Incremental apply when something changes.** Already true for Terraform
   (Milestone 10's `plan` on PR, manual `apply`) and for the 4 Deployments
   (`deploy` = `kubectl rollout restart`). Not true for the rest of the
   Kubernetes/database/external-service side.
2. **Full recreate from a torn-down state.** The one this initiative is
   really about. Recovering this stack from zero currently takes hours of
   hands-on sequencing and one-off `kubectl run` pods.

**Decisions taken during brainstorming (2026-09-18/19):**

- **Scope: full teardown.** `down` destroys every ephemeral, billable
  resource (VPC/NAT, EKS, nodes, RDS, Kafka/Mongo volumes, the ALB, IRSA
  roles). Not just the data plane.
- **Data is disposable.** Losing all data on `down` is acceptable
  (`skip_final_snapshot = true` on RDS stays). `up` recreates the schema and
  **seeds initial data**. No snapshot/restore logic.
- **Approach A** (below): human-dispatched `up`/`down` workflows using a
  dedicated cluster-admin `bootstrap` identity. GitOps (Argo CD) is a later
  follow-on, not this initiative.

**Not yet a numbered milestone in `AWS_PLAN.md`.** Milestone 13 there is the
optional domain-registration candidate, so this would be added as a new
Milestone 14 once the spec is approved.

**Scope inventory from the prompt, and where each item lands:**

| Prompt item | Where it lands |
|---|---|
| 1. RDS `-target` destroy vs `create_before_destroy` | **Dropped.** A full teardown recreates RDS at whatever `allocated_storage` is in code; the fork only mattered for shrinking a live instance. Known limit: shrinking a live RDS = run `down` then `up`. |
| 2. Migration Job trigger | `up` stage 5 (delete + apply the Job) |
| 3. Two `GRANT rds_iam` bootstraps | `up` stage 5: Job 1 and Job 2 |
| 4. Publications Job | `up` stage 5 (same trigger shape as 2) |
| 5. Debezium slot recovery | **Out of scope.** `down`/`up` creates fresh slots, removing the common failure; the manual runbook stays documented. |
| 6. Debezium password rotation | **Folded in** (Job 2 + Secrets Manager). Rotation later = same path with a new value. |
| 7. PVC resize dance | **Moot** under teardown; stays a manual procedure for a live cluster. |

## Revisions made while writing the implementation plan (2026-09-19)

Reading the actual modules and manifests changed six things from the version
first approved; each is folded into the sections below:

1. **No new Environment.** `bootstrap` trusts the existing `github_trust`
   policy (main branch only, enforced by IAM); the `infra` jobs reuse
   `environment: aws-infra` and `terraform_apply` unchanged (D3).
2. **Three out-of-band Secrets, not one:** `debezium-db-credentials`,
   `mongo-consumer-seed-password`, `streaming-mongo-credentials` (D6).
3. **`/health/data-quality` fails closed** with HTTP 503 when no report row
   exists (`app/api/routers/health.py`), so the triggered `dbt-build` in
   stage 8 is required, not merely useful.
4. **Verified live, read-only:** the ALB carries tag `elbv2.k8s.aws/cluster`
   = cluster name; PVC-backed EBS volumes carry
   `kubernetes.io/cluster/<cluster>=owned`; the RDS master secret uses the
   AWS-managed `aws/secretsmanager` key; `AmazonEKSClusterAdminPolicy`'s exact
   ARN is already used in `modules/eks/main.tf`.
5. **`down` is two jobs** (`platform` then `infra`) because Kubernetes steps
   and AWS-level assertions run under different identities.
6. **GCP gets its own roots.** The `google` provider added to the CI-planned
   root in Milestone 12 makes `plan` fail without Google credentials, which CI
   does not have. GCP WIF moves to `terraform/gcp/` (D4); the uncommitted
   Milestone 11/12 work is landed as its own baseline PR after that move so
   every later PR's `plan` is readable. At the user's request CI then gets
   keyless GCP access, the same way it reaches AWS (D9), with a second
   local-only root for the trust anchor.

## Decisions and why

### D1. What `down` leaves alone

Persistent (never destroyed by `down`): the S3 state bucket, the ECR repos,
CI's own OIDC IAM roles, GCP WIF, the budget, and the SNS topic + email
subscription. Reasons, per item: destroying the state bucket or the OIDC
roles would delete the credentials/state CI is running on; destroying ECR
would force a full arm64 image rebuild before every `up`; the SNS
subscription needs a manual email confirmation each time it is created; the
GCP and budget resources already carry the project's "permanent, never torn
down" precedent (`prevent_destroy`).

### D2. Approach A, and what was rejected

**A (chosen):** `cluster-up` and `cluster-down` workflows, `workflow_dispatch`
only, using a new `bootstrap` role with EKS cluster-admin. Matches the
existing shape (four purpose-scoped OIDC roles, dispatch-triggered mutating
jobs). The routine, narrow `deploy` role is unchanged.

**B (rejected for now): GitOps via the EKS Argo CD capability.** From current
AWS docs: it runs in AWS-managed accounts outside the cluster (no node
memory), but requires AWS IAM Identity Center ("local users are not
supported"), pricing was not found, and a cold cluster still needs a
cluster-registration Secret applied. It is a new subsystem, not a fix for
this one. Best treated as a later follow-on; `AWS_PLAN.md` Milestone 10
already names GitOps as the next step for an always-on environment.

**C (rejected): Terraform-only, runbook above the cluster.** Delivers little
of what was asked.

### D3. The trust model (revised while writing the plan)

Earlier drafts assumed a required-reviewer GitHub Environment gating the
`bootstrap` role. That is not available: the Milestone 10 spec
(`2026-09-11-aws-milestone-10-cicd-design.md`) found required reviewers
unusable on this solo-owned repo (the picker never offers the owner's own
account). Reading `modules/github-oidc` while writing the plan showed a
stronger answer than a new Environment:

- **`bootstrap` uses the existing `github_trust` policy**, the one `deploy`
  and `ecr_push` already use, whose `sub` condition is
  `repo:OWNER@ID/REPO@ID:ref:refs/heads/main`. A `workflow_dispatch` run on
  any other branch carries a different `sub` and cannot assume the role, so
  "main only" is enforced by IAM itself, with no Environment protection rule
  or deployment-branch setting. "Manually confirmed" comes from the
  `workflow_dispatch` trigger, exactly as for `apply` and `deploy` today.
- **The `infra` jobs reuse `environment: aws-infra` and the existing
  `terraform_apply` role, unchanged.** No new Environment and no trust-policy
  edit.
- **Privilege context:** `terraform_apply` already holds
  `AdministratorAccess`, which can grant itself any EKS access entry, so a
  dispatch-only, main-only `bootstrap` cluster-admin role adds little beyond
  what the pipeline already has. The RBAC-objects-inside-`k8s/overlays/aws`
  concern (`WHATS_NEXT.md`, deploy-redesign entry) applies to *routine
  automatic* identities and is **not required by this design**; it becomes
  required only if `deploy`'s scope is ever widened.

### D4. Terraform roots, split by lifecycle and by who may apply them

`terraform/main.tf` today holds one root in which CI's own identity and the
ephemeral stack are cross-wired: `module.github_oidc` takes
`module.eks.cluster_arn`, and `module.eks` takes
`module.github_oidc.deploy_role_arn`. ECR, S3 and GCP WIF carry
`prevent_destroy`, so a plain `terraform destroy` cannot work, and a
CI-run destroy would try to delete the OIDC roles it authenticates with.

| Root | State key | Contents |
|---|---|---|
| `terraform/` (existing, unchanged state) | `events-api/terraform.tfstate` | `s3`, `ecr`, `github_oidc` (+ new `bootstrap` role), `budget.tf`, SNS topic + subscription, **new** Secrets Manager secret container for the Debezium credential |
| `terraform/cluster/` (new) | `events-api/cluster.tfstate` | `networking`, `eks`, `rds`, `iam` (IRSA roles + viewer role), the dbt-failed CloudWatch alarm, the `bootstrap` EKS access entry |
| `terraform/gcp/` (new) | `events-api/gcp.tfstate` | `gcp_wif` (Kafka Connect's GCP workload identity pool, provider, two IAM bindings). Local-only at first: with no Google credentials `terraform plan` fails ("could not find default credentials", verified 2026-09-19), so it cannot sit in a root CI plans until CI has GCP access. Joins the `terraform.yaml` matrix in D9. |
| `terraform/gcp-bootstrap/` (new, D9) | `events-api/gcp-bootstrap.tfstate` | GitHub pool/provider, the two CI service accounts and their project-level grants. **Local-only permanently.** |

- **Coupling removed:** `github_oidc` receives the cluster ARN computed from
  the fixed cluster name (no dependency on the resource); `cluster/` looks
  up the deploy and bootstrap roles by name (`data "aws_iam_role"`).
  Dependency runs one way: `cluster/` reads persistent identities.
- **`down` = a plain `terraform destroy` in `cluster/`.** No `-target`, no
  `count`-gated modules. A mistake in `cluster/` cannot touch persistent state.
- **The alarm moves into `cluster/`.** `observability.tf`'s alarm uses
  `treat_missing_data = "breaching"`; left persistent, every teardown would be
  expected to send an ALARM email and `up` an OK email. The SNS topic and
  subscription stay persistent so the email confirmation is not repeated.
- **`terraform.yaml`** runs `plan` for both roots on PRs touching
  `terraform/**`; its manual `apply` gains a `stack` input
  (`foundation` default | `cluster`). This keeps capability 1 (incremental
  apply) working for both roots.
- **One-time migration (no state surgery, no `-target`):**
  1. Apply the persistent-root changes first (decoupling, `bootstrap` role,
     secret container), through the existing PR-plan / manual-apply pipeline.
  2. Run the `down` Kubernetes steps once against the live cluster, locally
     with the operator's own admin access (delete the Ingress and wait for the
     ALB to disappear, delete the PVCs), because the `bootstrap` access entry
     will not exist on the old cluster.
  3. In one PR, delete the `networking`, `eks`, `rds` and `iam` module blocks
     (and their outputs) from the old root and add `terraform/cluster/`.
     Terraform destroys resources that are in state but no longer in
     configuration, so the existing manual `apply` (`stack=foundation`)
     performs the teardown.
  4. Dispatch `cluster-up`. That first run creates everything in `cluster/`
     and doubles as the cold-start test.

### D5. Nothing infrastructure-specific is hand-typed

Terraform outputs are the single source of truth. Inventory of what is
hardcoded today (`k8s/`, `helm/`, checked 2026-09-18):

| Value | Occurrences | Fix |
|---|---|---|
| RDS host `events-api-db.choe4u6ye3yf…` | 6 lines in 4 files (app configmap, dbt CronJob, publications Job ×2, Debezium connectors ×2) | runtime ConfigMap |
| Region `eu-central-1` | app configmap, dbt CronJob, publications Job command, ALB Helm values | runtime ConfigMap; Helm `--set` |
| Cluster name `events-api-eks` | ALB Helm values | Helm `--set clusterName=` from `terraform output` |
| Account ID `938500344309` | 11 occurrences in 10 files (5 IRSA role ARNs, 6 ECR image URIs) | Kustomize substitution |
| GCP project number, BigQuery project ID | `gcp-wif-credential-config.yaml`, `bigquery-connectors.yaml` | **Left hardcoded on purpose:** keyed to `prevent_destroy` resources that never change across a teardown; making the credential JSON dynamic is the deferred Milestone 12 item. |

- **Runtime values (RDS host, region):** `up` writes one ConfigMap
  (`infra-endpoints`) from `terraform output` after stage 1; consumers read it
  as env. Kafka Connect already enables `config.providers: env`
  (`EnvVarConfigProvider`) in `kafka-connect.yaml`, so `KafkaConnector` CRs use
  `${env:RDS_HOST}` exactly as they use `${env:DEBEZIUM_DB_PASSWORD}` today.
  Jobs switch to `valueFrom: configMapKeyRef`; the publications Job's inline
  `--hostname …` becomes `$PGHOST`.
- **Manifest-baked values (account ID):** Kustomize `images:` +
  `replacements`, sourced from the same generated values.
  `envsubst` with an explicit variable list is the fallback (chosen over bare
  `envsubst` because it must not touch the `$(cat …)` shell in Job specs).
- **New Terraform outputs needed:** `region`, `account_id`.

### D6. The Debezium credential

Facts: the `debezium_replication` role must stay password-authenticated
forever. AWS docs: "you can't use IAM authentication to establish a
replication connection" (PostgreSQL), and a user with `rds_iam` must log in
via IAM, so the master user `events` (which has `rds_iam`, needed by the
migration/dbt/publications Jobs) cannot be reused for Debezium either. The
master password is RDS-managed in Secrets Manager
(`manage_master_user_password = true`), so copying it into a Kubernetes
Secret would go stale, and AWS advises against master credentials in
applications.

Three things must agree: the DB role's password, the Kubernetes Secret
`debezium-db-credentials` (key `password`, currently created by hand; one of
three such Secrets, see below), and the connector env var.

Design: Secrets Manager holds the canonical value.

1. `terraform/` (persistent) creates the secret **container**
   `events-api/debezium-replication` (`prevent_destroy`), with **no value in
   Terraform** (so it never appears in state).
2. **Job 2** (IRSA role scoped to that one secret: `GetSecretValue`,
   `PutSecretValue`, `DescribeSecret` on its ARN, plus `GetRandomPassword`):
   if the secret has no value, generate one and store it; then
   `ALTER ROLE debezium_replication PASSWORD …` from the stored value.
3. After Job 2 succeeds, the `bootstrap` role pipes the value from Secrets
   Manager into `kubectl apply` for `debezium-db-credentials`, never echoed
   (masked). The Job's ServiceAccount needs **no** Kubernetes Secrets-write.
4. Kafka Connect and the connectors are applied after this, so the pod never
   sits in `CreateContainerConfigError`.

Result: the password is stable across teardown cycles (the migration recreates
the role with its dev password each time; Job 2 overwrites it within the same
`up`). Rejected: Terraform `random_password` (value in state), External
Secrets Operator (another operator on a cluster that already hit a memory
crunch).

**Mongo credentials (the other two out-of-band Secrets).** Verified from the
manifests and the Milestone 3 plan: `mongo-consumer-seed-password` (key
`password`) is the MongoDB Community Operator's user password;
the operator then generates `events-mongo-admin-cdc-consumer` (key
`connectionString.standard`); and `streaming-mongo-credentials`
(`STREAMING_MONGO_URI`) is a copy of that connection string that
`cdc-consumer` reads via `envFrom`. Mongo is destroyed on every `down`, so its
credentials have nothing to keep stable and **do not need Secrets Manager**:
`up` generates the seed password on the runner (only if the Secret does not
already exist, so a re-dispatch never rotates a live password), pipes it
straight into `kubectl apply`, waits for the operator to produce
`events-mongo-admin-cdc-consumer`, and copies its connection string into
`streaming-mongo-credentials`. Nothing is echoed. `cdc-consumer` is applied
after that Secret exists.

### D7. Seed

A plain Kubernetes Job in a new `k8s/overlays/aws-seed/` overlay, running the
app image. It waits for `/readyz`, then calls the app's own API:
`POST /admin/tenants` (no `X-Tenant-ID`; not tenant-scoped), then
`POST /events` with `X-Tenant-ID`. Going through the API exercises RLS and the
entire CDC path (Debezium → Kafka → Mongo projection, realtime SSE, BigQuery
sinks), not just SQL. Idempotence: a re-dispatched `up` skips the
Job once it has succeeded, and deletes and recreates it if it failed. Exact tenant/event
content is decided in the plan after reading the request schemas
(`app/domain/schemas.py`).

### D8. Images

Not splitting the multi-stage `Dockerfile`: the three Python runtime targets
share the `builder` stage on purpose (`builder-streaming` and `builder-dbt`
build `FROM builder`), a split would duplicate it and let it drift, and
nothing in `up`/`down` depends on how images are built. Instead:

- Replace the four copy-pasted `build-push` steps with a **matrix** (context,
  target, repo). Add the missing `kafka-connect` image (`kafka-connect/Dockerfile`,
  currently built by hand; the ECR module's default `repository_names`
  already lists it).
- `module.github_oidc`'s `ecr_repository_arns` lists only 4 repos, so the
  `ecr_push` role's ARN list must gain `kafka-connect` or the new matrix
  entry cannot push.

### D9. Keyless GCP access for CI (added at the user's request)

CI reaches AWS with no stored secrets (GitHub OIDC token -> role); Google
supports the same shape via Workload Identity Federation, so `terraform/gcp/`
can be planned and applied by CI. Design, verified against Google's
`google-github-actions/auth` documentation and `gcloud iam roles describe`:

- A `github-actions` pool and OIDC provider whose mandatory attribute condition
  pins the numeric repository and owner IDs.
- Two service accounts mirroring `terraform_plan` / `terraform_apply`, each
  impersonable only from one exact `sub`. Read-only role for plan; pool admin
  plus service-account admin (scoped to the `big-query` account only) for apply.
- **The trust anchor lives in a second local-only root, `terraform/gcp-bootstrap/`.**
  Workload identity pools have no IAM policy of their own, so the pool roles
  must be project-level grants; a Terraform-managed project grant needs
  project-level IAM admin, which would let CI grant itself anything. CI manages
  only `terraform/gcp/`.
- Residual risk equal to AWS's `terraform_apply`: an unprotected Environment can
  be declared by a workflow on any branch (solo-owned repo).
- Implemented as Plan 1, Task 9; independent of `up`/`down`, which never touch GCP.

## Architecture

### `cluster-up` (dispatch only)

Two jobs: `infra` (Terraform, `terraform_apply` role) then `platform`
(Kubernetes, `bootstrap` role). Non-secret values (endpoints, ARNs) pass via
job outputs. Idempotent and re-runnable; each stage waits with a timeout so a
failure stops the run and a re-dispatch resumes.

Dependency order (independent branches may run in parallel):

| # | Stage | Notes |
|---|---|---|
| 1 | `terraform apply` in `cluster/` (`infra` job, `terraform_apply`) | VPC/NAT, EKS + addons, RDS, IRSA roles, access entries, alarm |
| 2 | Write `infra-endpoints` ConfigMap | from `terraform output` |
| 3 | Helm `upgrade --install`: ALB controller, metrics-server, Strimzi, MongoDB operator, kube-prometheus-stack | pinned chart versions, values files already in `helm/`; wait for CRDs + rollouts. The ALB controller's ServiceAccount (`aws-alb-controller` overlay) is applied first because the chart runs with `serviceAccount.create: false`. No Helm/Kubernetes Terraform provider, same precedent as before |
| 4 | Namespace, ServiceAccounts, RBAC (`k8s/overlays/aws` base); Mongo seed-password Secret; start Kafka + Mongo (slow) in parallel | |
| 5 | **Job 1** (`GRANT rds_iam TO events`, master password read from Secrets Manager) -> **migration Job** -> **Job 2** (`GRANT rds_iam TO events_app`, Debezium credential, then `bootstrap` creates the Secret) -> **publications Job** | Jobs deleted and re-applied every `up` (as in today's runbook); all steps idempotent |
| 6 | After Mongo is ready, create `streaming-mongo-credentials`; then workloads: app, realtime, dbt CronJob, cdc-consumer, Kafka Connect, then Debezium + BigQuery connectors | connectors only after publications + Debezium Secret exist |
| 7 | **Seed Job** | skipped once it has succeeded; a failed one is deleted and recreated so it can never wedge later runs (a re-run after a partial failure may add a few extra demo tenants: acceptable for disposable data) |
| 8 | Verify | trigger one `dbt-build` (**required**: `/health/data-quality` returns 503 until a report row exists), then see Verification |

**Bootstrap Jobs.** Same shape as the existing migration/publications Jobs: a
scoped IRSA ServiceAccount, an `initContainer` (aws-cli image) that fetches
the secret/token, a main container (`postgres:18`) running `psql`. Job 1 is
the one step that cannot use IAM auth: on a fresh instance `events` can only
authenticate with the master password until `GRANT rds_iam TO events` lands
(observed live twice last session). The in-pod equivalent of "never through a
local shell" is the initContainer writing to a memory-backed `emptyDir` that
only the main container mounts. Job 2 is expected to connect as `events` over
IAM (the grant landed in Job 1); confirm at plan time.

### `cluster-down` (dispatch only)

Two jobs, in this order, because they need different identities:

1. **`platform` job (`bootstrap`):** delete the Ingress and wait for it to
   finish deleting (the ALB controller removes the ALB it created; Terraform
   has no record of it), then delete all PVCs in the `events-api` and
   `monitoring` namespaces explicitly, rather than relying on
   operator/StatefulSet cleanup behaviour (not verified either way). The
   `gp3` StorageClass sets no `reclaimPolicy`, which should default to
   `Delete`. The Ingress is the only load balancer in the manifests; there
   are no `type: LoadBalancer` Services.
2. **`infra` job (`terraform_apply`, `environment: aws-infra`):** assert no
   ALB tagged `elbv2.k8s.aws/cluster=events-api-eks` remains, `terraform
   destroy` in `cluster/`, then assert no orphaned billable resources: EBS
   volumes tagged `kubernetes.io/cluster/events-api-eks=owned`, load
   balancers, NAT gateways, unattached EIPs. (Both tag filters were verified
   against the live account on 2026-09-19.)

### Identities (one workload, one identity)

| Role | Status | Trust | Can |
|---|---|---|---|
| `terraform_apply` | existing, `AdministratorAccess`, **unchanged** | `aws-infra` Environment | apply/destroy both roots |
| `events-api-github-bootstrap` | **new**, in `github-oidc` | `github_trust` (main branch only) | `eks:DescribeCluster` on the cluster ARN; EKS cluster-admin access entry (created in `cluster/`); `secretsmanager:GetSecretValue` on the Debezium secret only |
| `events-api-iam-bootstrap-master-irsa` (Job 1) | **new**, in `cluster/` | ServiceAccount `events-api-bootstrap-master` | `secretsmanager:GetSecretValue` on the RDS master secret ARN. No `kms:Decrypt` statement: the secret uses the AWS-managed `aws/secretsmanager` key (verified); first-run evidence confirms |
| `events-api-iam-bootstrap-roles-irsa` (Job 2) | **new**, in `cluster/` | ServiceAccount `events-api-bootstrap-roles` | `rds-db:connect` as `events`; `Get/Put/DescribeSecret` on the Debezium secret only; `secretsmanager:GetRandomPassword` |
| `terraform_plan` | existing | pull_request | its state-lock policy gains the `events-api/cluster.tfstate.tflock` key |
| `ecr_push` | existing | main | ARN list gains `kafka-connect` |
| `deploy` | existing, unchanged | main | rollout-restart the 4 Deployments |

### Manifest changes

Overlay chaining today: `aws-realtime` -> `aws-cdc` -> `aws` -> `base`;
`aws-dbt` and `aws-observability` also chain through `aws`; `aws-alb-controller`
is standalone (a `kube-system` ServiceAccount). Re-applying a shared base
several times is idempotent, so `up` applies the leaf overlays in sequence.
Changes:

- New `k8s/overlays/aws-bootstrap/` (Job 1, Job 2, their ServiceAccounts).
- New `k8s/overlays/aws-connect/` holding what must start *after* the
  credentials exist: Kafka Connect, both connector files,
  `gcp-wif-credential-config.yaml`, and the `cdc-consumer` Deployment, all
  moved out of `aws-cdc`.
- New `k8s/overlays/aws-seed/` (the seed Job).
- Consumers of RDS host / region read the `infra-endpoints` ConfigMap; account
  ID comes from Kustomize substitution (D5).

## Verification (acceptance bar)

1. From a torn-down state, dispatching `cluster-up` reaches green with **no
   local commands**: app reachable via the ALB; a real `POST /events` visible
   in the Mongo projection, both `realtime` SSE streams, and both BigQuery
   tables; all 4 connectors `RUNNING`; one `dbt-build` triggered from the
   CronJob; `/health/data-quality` returns 200.
2. `cluster-down` reaches green and the AWS CLI shows none of: the EKS
   cluster, the RDS instance, NAT gateways, the VPC, ALBs, cluster EBS
   volumes, EIPs. Persistent resources are still present and `plan` on both
   roots is clean.
3. A **second** `cluster-up` from zero also works (proves repeatability and
   Debezium-password stability).
4. Re-dispatching `cluster-up` on a live cluster is a no-op apart from the
   Job re-runs (bootstrap, migration, publications); the seed does not rerun.
5. Existing pipelines are unaffected: PR `plan` for both roots, `build-push`
   including `kafka-connect`, `deploy`.
6. No secret values in workflow logs or in either Terraform state (spot-check
   the Debezium password specifically).

## Prerequisites before the first real `up`

(Plan 1, Task 1b lands all of this as a reviewed baseline PR, after Task 1 relocates GCP.)

Uncommitted at the time of writing (`git status`, 2026-09-18); CI builds and
deploys from `main`, so these must land there first, at the user's discretion:
the `Dockerfile` `dbt deps` fix (without it the CI-built dbt image is broken),
last session's Helm-values and manifest right-sizing, the Terraform/EKS/RDS
changes, and `terraform/modules/gcp_wif/` + `import_gcp_wif.tf`.

## Verify at plan time (unverified in this session)

Resolved during plan-writing and therefore removed from this list: Environment
mechanics (no Environment used, D3), the EKS access-policy name, the
data-quality fresh-DB behaviour, the RDS-secret KMS key type, and the ALB/EBS
orphan-detection tags.

Still to confirm, each with an explicit check in the plan:

- Kustomize `replacements` accepting a generator-produced ConfigMap as its
  source (D5); fallback is `envsubst` with an explicit variable list.
- Whether Job 2 can connect as `events` over IAM and grant `rds_iam` to
  `events_app`.
- Whether `GRANT rds_iam` (and `ALTER ROLE`) are clean no-ops on re-run (Jobs
  run every `up`); tested against a local Postgres in the plan.
- That the ALB controller's finalizer makes `kubectl delete ingress --wait`
  block until the ALB is gone (the `infra` job's tag assertion is the backstop
  either way).
- Native arm64 runners and BuildKit layer caching for the image matrix
  (builds are currently arm64 under QEMU on an amd64 runner).
- After `down`, that PVC deletion releases the EBS volumes (assertion in the
  `infra` job).

## Out of scope / known limits

- Data is lost on every `down`, by design.
- A change to non-Deployment manifests (a Kafka setting, a Helm value) is not
  applied automatically on merge; it needs a re-dispatch of `cluster-up`.
- GitOps (Argo CD) is a later follow-on.
- Debezium slot recovery and password rotation on a *live* cluster remain
  documented manual procedures (rotation reuses Job 2's path).
- Shrinking a live RDS instance = `down` then `up`.
- GCP IDs stay hardcoded (D5).
- Multi-environment/domain routing (Milestone 13 candidate) is unrelated.
