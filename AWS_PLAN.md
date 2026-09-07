# AWS Plan: Real-AWS/EKS Phase

**Before starting implementation here, read `WHATS_NEXT.md` first** — this
document references current project state (existing modules, existing
credential patterns, existing manifests) rather than re-explaining it; the
resumption notes are what confirm any of that is still accurate.

## Context

`PLAN.md`'s Context section named this explicitly from the start: "A real-AWS/EKS
deployment is a deliberately separate follow-up plan, not part of this one." This
document is that follow-up — the one piece of scope named but never written, now
that Milestones 0-13 (`PLAN.md`) and the job-posting gap-closing work
(`FUTURE_PLAN.md`, plus the docker-compose→kind migration and dbt scheduling work
done directly in `WHATS_NEXT.md`) are all done.

**Grounded in what already exists, not designed from scratch:**
`terraform/modules/{networking,iam,s3}` were already built provider-agnostic in
Milestone 5 (`use_locastack` toggles a LocalStack endpoint override — designed from
day one to point at real AWS with no rewrite). `k8s/base` + `k8s/overlays/{cdc,dbt}`
are what actually needs to run on EKS instead of kind. The credential-hygiene
pattern already established (the migration-job/app Postgres role split, least
privilege throughout) is what IRSA extends to real AWS IAM, not something this
phase invents fresh.

**Milestone 4's service now already exists, too** — `realtime/` (the
Node/TypeScript SSE relay) and `k8s/overlays/realtime/` were built and proven
end-to-end on kind before this phase started (see `WHATS_NEXT.md`'s pre-AWS
local-proof entry): the independent-per-pod consumer group fan-out, the
no-Redis design, and the SSE broadcast itself are all already validated live,
two replicas producing byte-identical output including a real captured
change, not just a snapshot replay. Milestone 4 below is kept as the original
design record; its actual remaining work on EKS is porting the deployment
target (ECR image, an EKS-side overlay, ALB routing), not building or
debugging the service logic itself.

**One real gap found while scoping this**: `terraform/providers.tf`'s
`access_key = "test"`/`secret_key = "test"` are hardcoded *unconditionally*, not
gated behind `use_locastack` the way the `endpoints` block already is. LocalStack
ignores credentials entirely so this never mattered before — pointing at real AWS
needs this fixed first.

**Why this document covers more than the job posting's literal stack list, unlike
`FUTURE_PLAN.md`**: `FUTURE_PLAN.md` was scoped by matching the posting's stack
line by line, deliberately excluding things that didn't close a named gap (Segment,
a TypeScript rewrite). This document initially made the same kind of
scope-minimization calls (skip Prometheus/Grafana, HPA, RBAC, CI/CD — "doesn't
teach anything this project hasn't already proven") — and that was the wrong
optimization target. This project's actual purpose is interview preparation:
broad, defensible, hands-on familiarity with the tools a Senior Data Engineer role
will actually ask about, not minimizing what gets built. Corrected mid-scoping;
the milestone list below reflects the corrected reasoning, not the original
narrower one.

**Cost/teardown discipline**: every milestone gets its own `terraform apply`/
`terraform destroy` cycle, same discipline Milestone 5's LocalStack work already
established. Nothing stays running between sessions — an idle EKS control plane
alone runs ~$73/month, before any worker nodes or RDS.

## Milestones

### 0. AWS foundation — done, see `WHATS_NEXT.md`

- Real IAM credentials for Terraform, replacing the currently-unconditional
  `"test"`/`"test"` in `providers.tf` — made conditional on `use_locastack`.
  **Correction found while implementing**: this needs a per-argument ternary
  on each scalar (`access_key`, `secret_key`, every `skip_*` flag,
  `s3_use_path_style`), not the `dynamic` mechanism the `endpoints` block
  uses — `dynamic` only generates nested blocks, never scalar arguments.
- Billing alerts (a real budget, not LocalStack's implicit $0).
- Real S3 remote state: `terraform/backend.tf` already has the `backend "s3"`
  block written (currently commented out, per Milestone 5's teardown) — this
  points it at a real bucket instead of LocalStack's.

### 1. VPC + EKS + ECR

- Extends the existing, already-provider-agnostic `networking` module for a real
  VPC suitable for EKS (public/private subnets across AZs, NAT).
- New `eks` Terraform module — the actual "real Kubernetes" this whole phase
  exists to prove, closing "Infrastructure as code and containerisation" for real
  rather than against a local stand-in.
- New `ecr` Terraform module, replacing `kind load docker-image` with real image
  pushes for all four images (app, streaming, dbt, plus the new Node service from
  Milestone 4).

### 2. RDS + the app, IRSA + RDS IAM auth throughout

- New `rds` Terraform module. `k8s/base/postgres-deployment.yaml`/
  `postgres-service.yaml` are removed entirely for this overlay — RDS replaces
  self-hosted Postgres, not just repoints an existing Service.
- Every Postgres connection (the app, the migration Job, the CDC consumer) uses
  IRSA-backed RDS IAM auth instead of a static password in a k8s Secret — the
  same least-privilege instinct already applied at the DB-role layer (the
  migration-job/app split), now extended to credential *lifetime* too.
  - Short-lived Jobs (migration, dbt) generate a fresh token per invocation —
    trivial, the 15-minute token window is irrelevant to a process that opens
    one connection and exits.
  - The long-running app Deployment uses a SQLAlchemy `creator` callable that
    mints a fresh token via `generate_db_auth_token()` on each new pooled
    connection, paired with `pool_recycle` — a known, bounded pattern (the token
    only matters at connection-open time; an already-open connection is
    unaffected by the token's expiry).
- New `k8s/overlays/aws/` overlay — genuinely new, not a rewrite of `k8s/base` or
  the existing `cdc`/`dbt` overlays, which stay exactly as they are for local
  kind work. Real ECR image URIs, real `imagePullPolicy`, IRSA ServiceAccount
  annotations.

### 3. Real Kafka and MongoDB, the production-pattern way

**Scoped in detail before implementation** (2026-09-04 brainstorming session,
against the still-live Milestone 1-2 infra — EKS/RDS were never actually torn
down at the end of that session, `terraform state list` confirmed it). Findings
below are decisions, not yet applied.

- Kafka → **Strimzi Operator** (installed via Helm CLI, not a Terraform
  `helm_release` — this repo has no Kubernetes/Helm Terraform provider
  anywhere, and raw `helm install` matches Milestone 6's own precedent), not
  the hand-rolled StatefulSet built during the kind migration. Mirrors the same
  arc as Postgres→RDS: kind proved the raw StatefulSet/PVC mechanics
  deliberately (that was the whole point of that migration); this phase
  graduates to the dominant real-world pattern — an Operator that manages
  broker identity, PVCs, and rolling upgrades declaratively, which is *why*
  hand-rolling Kafka is comparatively rare in real production. Single-broker
  KRaft (`replicas: 1`, with `default.replication.factor`/
  `offsets.topic.replication.factor`/etc. all overridden to 1 — a single
  broker can't satisfy Strimzi's normal RF defaults), no Entity Operator
  (Topic/User CRDs not needed here, one fewer pod). Confirmed via
  `docker buildx imagetools inspect`: Strimzi 1.2.0's operator image and its
  Kafka broker image (`quay.io/strimzi/kafka:1.2.0-kafka-4.3.1`) both ship
  `linux/arm64`, so this runs on the existing Graviton (`t4g.small`) node
  group with no image-architecture surprise.
- **Kafka Connect also moves to Strimzi's own CRDs** (`KafkaConnect` +
  `KafkaConnector`), not a hand-rolled Deployment + curl-based registration
  Job — corrected mid-scoping from an earlier draft of this plan that would
  have kept the kind migration's bare Deployment pointed at
  `debezium/connect:3.0.0.Final` directly. That image's entrypoint/
  config-injection convention isn't Strimzi's, so pointing `KafkaConnect.spec.
  image` straight at it is not a safe assumption. The correct, documented
  pattern instead: a new custom image `FROM quay.io/strimzi/kafka:1.2.0-kafka-
  4.3.1` with Debezium's Postgres connector plugin jars layered in, built and
  pushed to a new `kafka-connect` ECR repo (5th entry in `modules/ecr`'s
  `for_each`, alongside `app`/`streaming`/`dbt`/`realtime`) via the same
  Dockerfile/ECR pipeline every other image in this repo already uses — not
  Strimzi's in-cluster Kaniko `build:` mechanism, which needs extra in-cluster
  permissions this project has no other reason to grant. Connectors themselves
  become two `KafkaConnector` manifests (declarative, applied via
  kubectl/Kustomize) instead of `k8s/overlays/cdc`'s idempotent-curl-script
  Job — fully consistent with the broker being Operator-managed, not a
  half-migration.
- MongoDB → **MongoDB Community Operator** specifically (resolved from the
  original "a Helm chart or the Community Operator" hedge) — deliberately
  **not** Amazon DocumentDB. DocumentDB is API-compatible but not built on
  MongoDB's actual codebase (a different engine under a similar API surface),
  with real, known gaps in aggregation operators, index types, and transaction
  semantics. The posting names real MongoDB specifically; DocumentDB would
  weaken that claim under any real technical question, even though this
  project's actual Mongo usage (simple `replace_one(upsert=True)`, no
  transactions, no change streams) wouldn't personally hit most of those gaps.
  The Operator over a Helm chart specifically: Milestone 6 already proved
  "consume a vendored Helm chart" as a skill (Bitnami Postgres); the Operator
  teaches the CRD-based lifecycle-management pattern a second time in a
  different domain, the more interview-relevant repeat alongside Strimzi.
  Confirmed via `docker buildx imagetools inspect`: all four Community
  Operator component images (operator `0.13.0`, agent `108.0.6.8796-1`,
  version-upgrade-hook `1.0.10`, readinessprobe `1.0.23`) ship `linux/arm64`.
  `members: 1`, not the chart's default 3 — deliberately, to keep pod/PVC count
  down given the node-capacity finding below; this does mean real SCRAM auth
  and a `?replicaSet=` connection string, a genuine (if small) change to
  `streaming/mongo.py`/`streaming/config.py` versus kind's unauthenticated bare
  `mongo:7`.
- **Node capacity, a real gap found while scoping**: the live node group is a
  single `t4g.small` (1930m CPU / 1.36GiB allocatable, 5 pods already on it
  before any of this). Summing real request values for every new pod (Kafka
  broker 250m/512Mi, Kafka Connect 250m/512Mi, the consumer 100m/128Mi, the
  Strimzi Cluster Operator 200m/384Mi — its own Helm chart's documented
  default, the Community Operator's operator pod 500m/200Mi, one Mongo
  replica-set member ~450m/712Mi) against the app's existing 100m/128Mi comes
  to roughly 1.85 vCPU / 2.5GiB total requested. `desired_size` needs to go
  1→2 (already within the existing `max_size = 2`, no Terraform limit change)
  to fit this at all; even at 2 nodes memory utilization lands around 92% of
  allocatable — tight, not broken. Fallback if pods sit `Pending`: bump
  `max_size`/`desired_size` to 3 (~$13/mo more, cheap next to the EKS control
  plane's ~$73/mo baseline already running).
- CDC against RDS specifically: `rds.logical_replication` set on a
  `aws_db_parameter_group` (family `postgres18` — confirmed via
  `aws rds describe-db-engine-versions --engine postgres --default-only`
  against the live account, not assumed; RDS has no direct `postgresql.conf`
  access, unlike self-managed Postgres's `wal_level=logical` flag),
  `apply_method = "pending-reboot"` since it's a static parameter — needs a
  real manual `aws rds reboot-db-instance` after `terraform apply`, same
  "real infra needs a real reboot" lesson as Milestone 2's storage-encryption
  rebuild. **No new security group needed** — `modules/rds`'s existing
  `db_postgres` ingress rule already references `cluster_security_group_id`,
  whose own output docstring (Milestone 1) already states it's the correct
  ingress source for pods too, not just nodes; this already satisfies what
  this bullet originally called "real VPC security groups gating Kafka
  Connect's access to RDS" — nothing new to build there, a found-already-
  covered item, not a gap. Debezium's own DB credential: a new, dedicated k8s
  Secret manually populated from the RDS master password
  (`aws secretsmanager get-secret-value`) — same one-off-bootstrap precedent
  as Milestone 2's `GRANT rds_iam` step; not IRSA, since Debezium has no
  mechanism to refresh a 15-minute IAM token on its long-lived replication
  connection. Whether the master user already carries `rds_replication` or
  needs an explicit `GRANT` is still to be verified empirically against the
  live instance during implementation, not assumed.
- New `gp3` `StorageClass` for Kafka/Mongo's storage, provisioned via the AWS
  EBS CSI driver installed as a **Terraform-native `aws_eks_addon`** (new IRSA
  role trusting the existing OIDC provider, `AmazonEBSCSIDriverPolicy` under
  its `service-role/` path, `resolve_conflicts_on_create` per the v6 provider
  argument split Milestone 1 already logged) — not Helm, since that's the
  cleaner IaC path for an infra-level add-on (distinct from Strimzi/Mongo's own
  Helm-based install, which is the actual standard path for *them*). The
  `StorageClass` itself: `volumeBindingMode: WaitForFirstConsumer` (EBS volumes
  are AZ-bound; immediate binding risks a PVC provisioned in the wrong AZ for
  the pod that needs it), `allowVolumeExpansion: true`, and deliberately **not**
  marked cluster-default — EKS already ships a default `gp2`, and two defaults
  is an error state; Kafka's and Mongo's volume specs reference `class: gp3`
  explicitly instead.
- New `k8s/overlays/aws-cdc/` (layers on `../aws`, the same chaining pattern
  `k8s/overlays/realtime` already uses on `../cdc`) holds what Kustomize
  actually owns here: the `KafkaConnector` manifests, the publications-creation
  script retargeted at the RDS hostname, the consumer Deployment repointed at
  Strimzi's `<cluster>-kafka-bootstrap:9092`, and the `gp3` `StorageClass`.
  Strimzi's and Mongo's own Helm values/CRs (`helm/strimzi/values.yaml`, the
  `Kafka`/`KafkaConnect`/`MongoDBCommunity` CRs) get committed as real files
  too, not left as shell history — this project tears down and rebuilds
  per-milestone, so reproducing the whole stack from git is load-bearing here,
  the same reasoning `k8s/overlays/dbt`'s `configMapGenerator` already applied
  to its own scripts.
- Verification staged so a capacity problem shows up as an isolated failure,
  not a pile of unexplained `Pending` pods: `terraform apply` → manual RDS
  reboot → confirm `SHOW wal_level = logical` → `gp3` `StorageClass` proven
  with a throwaway PVC → Strimzi + `Kafka` CR `Ready` → `MongoDBCommunity` CR
  `Ready` → publications + `KafkaConnector`s registered → consumer running →
  the actual bar from this document's own Verification section below:
  `POST /events` → Kafka → consumer → Mongo, live.

### 4. Node/TypeScript real-time relay

A genuinely new service, not a redundant rewrite of anything Python already
does — reflects real, existing Node/TypeScript experience (the posting names it
as "nice to have"), applied to a well-motivated architectural niche rather than
inflated into a technical-necessity claim it doesn't hold.

**Already built and proven on kind, not just planned** — see the Context
section above and `WHATS_NEXT.md`. What follows is the design as scoped going
in; it now doubles as a description of what's already running.

- Its own, independent Kafka consumer group (deliberately **not** shared across
  replicas) subscribed to `cdc.public.events`/`cdc.public.tenant_accounts` — the
  same topics the existing Python consumer already reads, but a separate group
  ID tracking separate offsets. Demonstrates a real Kafka pattern this project
  hasn't shown yet: multiple independent consumer groups against the same
  topics for genuinely different purposes (durable audit-log projection vs.
  ephemeral live fan-out).
- **SSE, not WebSockets** — the actual data flow is unidirectional (server
  pushes CDC updates; nothing needs to flow back over this channel), so
  WebSockets' bidirectional capability would be unused overhead. SSE also gets
  free reconnection via the browser's native `EventSource` API, and needs no
  special ALB configuration (WebSockets typically need sticky sessions /
  specific target-group handling behind an ALB; SSE is just standard HTTP).
- No database access of its own — purely an in-memory relay of currently-open
  SSE connections, scoped per tenant (`X-Tenant-ID`, matching the existing
  convention). If a pod restarts, nothing meaningful is lost — a reconnecting
  client just picks up new events going forward.
- New endpoint: `GET /stream/events` (tenant-scoped), a long-lived SSE
  connection as the live-feed counterpart to `GET /events`'s point-in-time
  snapshot.
- **No Redis.** The one piece of this milestone's design that got a real
  correction while scoping it: multiple Node replicas sharing *one* Kafka
  consumer group would cause Kafka's partition-splitting to silently drop
  events for clients connected to the "wrong" pod — the standard industry fix
  for that is Redis pub/sub fan-out across replicas. But that problem only
  exists *because* of the shared-group assumption; giving each pod its own
  independent consumer group (Kafka's own broadcast-consumption pattern, not a
  new tool) solves it natively, with no new infrastructure. Documented
  explicitly so this doesn't get silently reintroduced later.
- **Honest framing, not oversold**: Python (`StreamingResponse` + the
  already-used `confluent-kafka` `AIOConsumer` + `asyncio`) could genuinely
  build the identical service — there's no hard capability gap forcing this
  into Node. The real reasons to build it in Node anyway: it mirrors how real
  companies with both Python and Node in their stack organize things (usually
  team-ownership/ecosystem-fit driven, not a technical wall), and it turns
  existing, real Node experience into a concrete, discussable project artifact
  — which is this project's actual goal.

### 5. BigQuery as a real warehouse, fed by the same CDC pipeline

**Scoped in detail before implementation** (2026-09-06 brainstorming session).
Milestone 12's GCP resources confirmed still live before designing on top of
them, not assumed: project `project-e8569bd6-524d-42fe-bb9` (project number
`10216729029` — the *number*, not the ID string, is what GCP's WIF
impersonation binding actually needs, a real distinction, not
interchangeable), service account `big-query@project-e8569bd6-524d-42fe-bb9
.iam.gserviceaccount.com` (not disabled), dataset `events_analytics` — all
still present via direct `gcloud`/`bq` calls against the live project.

- Kafka Connect gets a second, sink-side connector. **Connector choice
  corrected from the plan's original generic wording**: not WePay's original
  `kafka-connect-bigquery` (GitHub-flagged `DEPRECATED`) and not Confluent's
  own current version (different commercial terms) — **Aiven's fork**
  (`Aiven-Open/bigquery-connector-for-apache-kafka`, forked 2024 specifically
  to keep an actively-maintained, Apache-2.0 continuation), the same
  license-appropriateness reasoning already applied to choosing the MongoDB
  Community Operator over DocumentDB. Packaging still open: whether Aiven
  publishes a ready-to-use plugin archive/jar (their GitHub Releases page has
  attachments, but couldn't confirm the exact asset names before
  implementation) or whether this needs `mvn package` in a Dockerfile build
  stage — check this directly at implementation time rather than assume
  either way, same discipline as everything else in this plan.
- Authenticated via **IRSA chained with GCP Workload Identity Federation's AWS
  provider**: the EKS pod assumes an AWS IAM role (IRSA, no static AWS keys),
  then presents that AWS identity to GCP's WIF token exchange to get a
  short-lived GCP token impersonating the target service account — no key file
  anywhere in the chain, and the only way to reach BigQuery at all from this
  project's infrastructure (`kind` structurally can't: this org's
  `iam.disableServiceAccountKeyCreation` policy blocks key-based auth, and
  `kind` isn't GKE so there's no Workload Identity path from there either).
  **Verified this is actually plausible, not just planned**: the Aiven
  connector loads credentials via `GoogleCredentials.fromStream()` — Google's
  own standard Java auth method, which auto-detects credential type from the
  JSON's `type` field and transparently supports both a raw service-account
  key *and* a WIF `external_account` credential config. One thing still to
  verify empirically, not assumed: Aiven's fork added security restrictions
  requiring explicit allowlisting for some `credential_source` types
  (file/URL/executable-sourced); AWS-sourced credentials use a structurally
  different, first-party code path in `google-auth-library-java`
  (`AwsCredentials`, reading AWS's own EC2-metadata/env-var credential chain
  directly, not executing an external command), so it likely isn't subject to
  that same gating — but this needs confirming against the connector actually
  running, not just against its stated design.
  - Kafka Connect needs its **own dedicated IRSA role** for this — it
    currently has no ServiceAccount/IRSA at all. This role needs **zero AWS
    permissions attached**; its only job is proving "this is a legitimate
    AWS-authenticated caller" to GCP's WIF handshake via
    `sts:GetCallerIdentity`, the same minimal-purpose pattern already used
    elsewhere in this project.
  - GCP-side setup stays **manual `gcloud`, not Terraform** — same precedent
    Milestone 12 already established (this project's Terraform is
    AWS-provider-only; GCP resources have always been provisioned by hand).
    Real command shape, grounded against GCP's own current docs rather than
    memory: `gcloud iam workload-identity-pools create` (pool), `gcloud iam
    workload-identity-pools providers create-aws` (provider, needs
    `--account-id 938500344309` and an `--attribute-mapping` extracting the
    IRSA role's ARN from AWS's STS assertion), then `gcloud iam
    service-accounts add-iam-policy-binding` granting
    `roles/iam.workloadIdentityUser` to a `principalSet://` member built from
    the **project number** (`10216729029`), not the project ID — confirmed as
    a real, easy-to-get-wrong distinction directly against GCP's docs.
    `gcloud iam workload-identity-pools create-cred-config --aws` then
    generates the actual credential-config JSON Kafka Connect consumes — this
    file contains no secret material (it's routing/mapping config, not a
    key), safe to mount via a plain ConfigMap rather than a Secret.
- The dbt CronJob's `--target bigquery` run then builds a **new** mart from
  *that* replicated data — deliberately not touching `bq_daily_event_counts`
  (Milestone 12's model), which stays exactly as it is, already documented as
  a deliberate synthetic-data proof of partition-pruning/RLS concepts. This
  milestone gets its own model reading the real sink-connector-populated
  tables, so Milestone 5's actual point (a genuine "real CDC data all the way
  to a BigQuery mart" proof) doesn't disrupt or conflate with what Milestone
  12 already proved. This is what makes BigQuery a genuine warehouse instead
  of an isolated demo: "Kafka for data streaming between systems" and
  "BigQuery data warehouse" become one coherent flow instead of two
  disconnected checkbox items.

### 6. Observability: Prometheus/Grafana + CloudWatch, each doing what it's actually for

- `kube-prometheus-stack` (Helm) for live service metrics. The app gets
  instrumented (`prometheus-fastapi-instrumentator` — a small, standard
  addition exposing `/metrics`), producing a real Grafana dashboard and real
  PromQL queries against real traffic, not a theoretical setup.
- CloudWatch Alarm + SNS notification stays specifically for the dbt CronJob's
  pass/fail state (`data_quality_runs`, built earlier this session) — a
  one-shot batch job's exit status isn't naturally a continuous Prometheus
  metric without extra plumbing, so this is two different signal types handled
  by the tool each is actually suited to, not redundant tooling stacked for its
  own sake.
- Closes the posting's explicitly-named "monitoring, alerting, and incident
  response" gap — genuinely untouched anywhere in this project until now.

### 7. HPA, made genuinely verifiable

- A small load-generation script actually drives the app's CPU up, so a real
  scale-up is observable via `kubectl get hpa` and Grafana (Milestone 6) —
  not configured-and-never-exercised.

### 8. RBAC

- At least one hand-written `Role`/`RoleBinding` for a real, motivated case —
  the same least-privilege instinct already applied at the Postgres-role and
  IAM layers, now applied to the Kubernetes API itself.

### 9. ALB Ingress

- Real external access via the AWS Load Balancer Controller (installed via
  Helm — the AWS-documented standard path for it, unlike the EBS CSI driver's
  native-addon path in Milestone 3).
- Routes to **both** backend services: path-based routing, e.g. `/stream/*` →
  the Node SSE service (Milestone 4), everything else → the Python app.
  Replaces `kubectl port-forward` as the only way in — the one piece of
  "production readiness" that's been faked this entire project.

### 10. CI/CD

- GitHub Actions: run the existing `pytest` suite on push, build and push all
  four Docker images (app, streaming, dbt, plus the new Node service) to ECR on
  merge to main.
- Deployment automation gets documented as "how this extends to auto-deploy,"
  not fully built — this project's own teardown-after-session discipline means
  there's usually no live cluster to auto-deploy onto; a GitOps-style follow-on
  (ArgoCD, etc.) would be the real next step in an always-on environment.

### 11. Shrink storage to real minimums (destroy/recreate, deferred from Milestone 5)

- Originating incident: Milestone 5's RDS parameter-group work hit a
  storage-full outage. Root cause and fix are done (`max_slot_wal_keep_size`
  capping WAL retention per replication slot — see Milestone 5/`WHATS_NEXT.md`).
  This milestone is the separate, lower-priority cleanup of the sizing
  decisions made under pressure during that incident, not the fix itself.
- Every disk in this project was originally sized by round-number guess, not
  calculation: `allocated_storage = 20` (RDS) and Kafka `5Gi`/Mongo
  `2Gi`+`1Gi` were all set in the original Milestone 1-3 commits with no
  comment or rationale, never revisited. The incident then bumped RDS to
  `50`/`max_allocated_storage = 100` reactively — also round numbers, not
  calculated, still uncommitted as of this writing.
- Real verified minimums (checked against live AWS, not assumed): RDS on
  `db.t4g.micro`/postgres18 with `gp2` storage → **5 GiB**
  (`aws rds describe-orderable-db-instance-options`). EBS `gp3` (Kafka/Mongo
  PVCs) → **1 GiB** (confirmed via `aws ec2 create-volume --dry-run --size 0`,
  which returns the real floor in its error message).
- Actual usage at time of writing, for scale: Postgres data 8.3MB
  (`pg_database_size`), Kafka PVC 33Mi/5Gi (1%), Mongo data-volume 329Mi/2Gi
  (18%, mostly oplog/WiredTiger overhead not real data), Mongo logs-volume
  46Mi/1Gi (5%, already at the EBS floor).
- **Why this is destroy/recreate, not a config edit**: both RDS allocated
  storage and Kubernetes PVC storage are grow-only, confirmed against
  official docs, not assumed. AWS RDS docs, verbatim: *"You can't deallocate
  space."* Kubernetes docs, verbatim: *"You can only use the volume expansion
  feature to grow a Volume, not to shrink it."* Neither the live RDS instance
  (already at 50GB) nor the existing Kafka/Mongo PVCs can be edited back down
  — only destroyed and recreated.
- **Why this was deferred instead of done immediately**: real rebuild cost
  (redo `alembic upgrade head`, redo `create-publications.sql`, redo both
  replication slots — already rebuilt once this session after the storage-full
  incident dropped them — reconfirm IRSA/IAM auth, rebuild the Mongo replica
  set and replay the CDC projection) against a real savings of roughly
  $5-6/month (gp2 ≈ $0.119/GB-mo, gp3 ≈ $0.095/GB-mo, both AWS Pricing
  API-verified this session, eu-central-1). Not worth doing reactively right
  after the stack had just been stabilized from the original outage.
- When picked up: `terraform destroy -target=module.rds.aws_db_instance.this`
  (and re-apply with `allocated_storage = 5`, no `storage_type` override
  needed since `gp2` is already the default in use), then `kubectl delete pvc`
  for the Kafka/Mongo claims before re-applying `k8s/overlays/aws-cdc` with
  `size: 1Gi` in both `kafka-cluster.yaml` and `mongodb-community.yaml`. Redo
  the full CDC verification (publications, replication slots, both Debezium
  connectors, both BigQuery sink connectors) from scratch afterward — this is
  a full teardown of Milestone 3 and 5's data plane, not an isolated change.

### 12. Terraform-manage the GCP WIF resources (closes the AWS/GCP IaC asymmetry)

Deliberately last — a cleanup/completeness item, not something blocking any other milestone. Brings Milestone 5's manually-`gcloud`-provisioned GCP resources (the workload identity pool, its AWS provider, and the two IAM bindings on the BigQuery service account) under Terraform via the `google` provider, closing the asymmetry named when GCP was first scoped out of this project's Terraform (Milestone 12/BigQuery, then repeated for Milestone 5's WIF setup): AWS gets full destroy/recreate IaC, GCP has always been provisioned by hand.

- New `google` provider block in `terraform/versions.tf`/`providers.tf`, reusing the ADC credentials already set up for `dbt --target bigquery` — no new auth plumbing needed.
- New module covering `google_iam_workload_identity_pool`, `google_iam_workload_identity_pool_provider` (with the `attribute.aws_role` mapping and `attributeCondition` already live), and 2× `google_service_account_iam_member` (`workloadIdentityUser`, `serviceAccountTokenCreator`).
- **The real open question, to resolve at implementation time, not assumed here**: these resources already exist and are live. Bringing them under Terraform means either `terraform import`-ing each one (safer, no disruption to the running BigQuery sink connectors, but the exact import-ID format for a WIF provider and for an IAM member resource hasn't been verified against this project's actual resources) or destroying and recreating them (simpler HCL, but causes a real interruption — new pool/provider IDs mean the credential-config JSON has to be regenerated and reapplied before the sink connectors can authenticate again). Import is the better fit for this project's own precedent of treating GCP resources as long-lived rather than destroy/recreate-per-session, but isn't guaranteed to go cleanly on the first try.
- The credential-config JSON itself isn't a first-class Terraform resource — it's a `gcloud iam workload-identity-pools create-cred-config` CLI side effect. Decide at implementation time whether to keep that CLI step even after Terraform owns the underlying pool/provider, or hand-build the JSON via `templatefile()` matching the schema `google-auth-library-java` expects (`audience`, `subject_token_type`, `token_url`, `credential_source` including `imdsv2_session_token_url`).
- Rough estimate given when this was scoped: 1.5-2.5 hours, with the import step as the main source of uncertainty — flagged as an estimate, not a commitment, given this project's own repeated experience of unknowns running longer than expected.

## Explicitly skipped, not deferred

- **A full TypeScript/Node.js backend rewrite** — not what Milestone 4 is. One
  well-scoped, genuinely new service (the real-time relay) demonstrates real
  Node skill without redundantly re-implementing anything Python already does.
- **Segment** — SaaS dashboard configuration, not an engineering skill gap;
  same reasoning `FUTURE_PLAN.md` already used.
- **Amazon DocumentDB** — would weaken a real MongoDB claim under technical
  scrutiny; see Milestone 3.
- **A GitOps deployment controller (ArgoCD, Flux)** — genuinely valuable, but a
  distinct, larger topic from "CI builds and pushes images"; left as a named
  follow-on in Milestone 10 rather than built here.

## Production concepts this teaches (mapped to milestones)

| Concept | Milestone |
|---|---|
| Real cloud IaC (VPC, EKS, ECR) vs. LocalStack | 0-1 |
| Managed RDS, IAM database authentication, credential-lifetime hygiene | 2 |
| Kubernetes Operators as the real-world alternative to hand-rolled StatefulSets | 3 |
| RDS-specific CDC configuration (parameter groups, security groups) | 3 |
| Polyglot backend architecture, SSE vs. WebSockets, multi-replica Kafka fan-out | 4 |
| Cross-cloud credential federation (IRSA + GCP Workload Identity Federation) | 5 |
| CDC-fed data warehouse, not a synthetic demo | 5 |
| Application + infrastructure observability (Prometheus/Grafana + CloudWatch) | 6 |
| Autoscaling under real, generated load | 7 |
| Kubernetes RBAC | 8 |
| Real external access, multi-service ingress routing | 9 |
| CI-driven test/build/push pipeline | 10 |
| Multi-cloud Terraform (AWS + GCP providers), import vs. destroy/recreate trade-offs | 12 |

## Verification

- Milestone 0: `terraform plan`/`apply` succeeds against real AWS with real
  credentials, not LocalStack's `"test"`/`"test"`; state confirmed living in the
  real S3 bucket.
- Milestone 1: a real EKS cluster is reachable via `kubectl`; an image pushed to
  ECR and pulled by a pod confirms the registry path end-to-end.
- Milestone 2: the app connects to RDS using a live-generated IAM token, not a
  static password — confirmed by rotating/expiring a token mid-session and
  observing the next new connection still succeeds via a freshly-minted one.
- Milestone 3: Debezium captures a real change from RDS with logical replication
  enabled only via the Parameter Group; Strimzi-managed Kafka and the
  Helm/Operator-managed MongoDB both pass the same round-trip proof already used
  during the kind migration (`POST /events` → Kafka → consumer → Mongo).
- Milestone 4: two browser tabs connected to different Node pod replicas both
  receive the same live event within milliseconds of each other, proving the
  independent-consumer-group fan-out actually works without Redis.
- Milestone 5: `dbt build --target bigquery` succeeds from an EKS pod with zero
  static credentials configured anywhere, and the resulting mart's row count
  matches real replicated CDC data, not a fixed synthetic count.
- Milestone 6: a real Grafana dashboard shows live request-rate/latency panels
  during an actual `curl` burst; a deliberately-broken dbt CronJob run produces
  a real CloudWatch Alarm state change and an SNS notification.
- Milestone 7: `kubectl get hpa` shows a real replica-count change during the
  load-generation script's run, reverting afterward.
- Milestone 8: a pod using the custom Role can perform exactly the permitted
  action and is denied everything else, confirmed via `kubectl auth can-i`.
- Milestone 9: the ALB's public DNS name serves both `/stream/events` (Node) and
  every other route (Python) correctly, with no `port-forward` involved.
- Milestone 10: a push to a feature branch runs tests in GitHub Actions; a merge
  to main results in four new image tags actually present in ECR.
- Milestone 12: `terraform plan` reports zero drift against the already-live GCP
  WIF pool/provider/bindings after import; the BigQuery sink connectors still
  authenticate successfully afterward (or, if the credential-config was
  regenerated, verified functionally identical to before).
