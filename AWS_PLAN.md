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

- Kafka → **Strimzi Operator** (installed via Helm), not the hand-rolled
  StatefulSet built during the kind migration. Mirrors the same arc as
  Postgres→RDS: kind proved the raw StatefulSet/PVC mechanics deliberately (that
  was the whole point of that migration); this phase graduates to the dominant
  real-world pattern — an Operator that manages broker identity, PVCs, and
  rolling upgrades declaratively, which is *why* hand-rolling Kafka is
  comparatively rare in real production.
- MongoDB → real, **self-hosted** MongoDB via a Helm chart or the MongoDB
  Community Operator — deliberately **not** Amazon DocumentDB. DocumentDB is
  API-compatible but not built on MongoDB's actual codebase (a different engine
  under a similar API surface), with real, known gaps in aggregation operators,
  index types, and transaction semantics. The posting names real MongoDB
  specifically; DocumentDB would weaken that claim under any real technical
  question, even though this project's actual Mongo usage (simple
  `replace_one(upsert=True)`, no transactions, no change streams) wouldn't
  personally hit most of those gaps.
- CDC against RDS specifically: `rds.logical_replication` set on a DB Parameter
  Group (RDS has no direct `postgresql.conf` access, unlike self-managed
  Postgres's `wal_level=logical` flag) plus real VPC security groups gating
  Kafka Connect's access to RDS — the genuinely new, AWS-specific lesson this
  milestone is actually for.
- New `gp3` `StorageClass` for Kafka/Mongo's `volumeClaimTemplates`, provisioned
  via the AWS EBS CSI driver installed as a **Terraform-native
  `aws_eks_addon`** — not Helm, since that's the cleaner IaC path for an
  infra-level add-on (distinct from Strimzi/Mongo's own Helm-based install,
  which is the actual standard path for *them*).

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

- Kafka Connect gets a second, sink-side connector — the established BigQuery
  Sink Connector, mirroring the Postgres *source* connector already running —
  streaming `cdc.public.events`/`cdc.public.tenant_accounts` into real BigQuery
  tables.
- Authenticated via **IRSA chained with GCP Workload Identity Federation's AWS
  provider**: the EKS pod assumes an AWS IAM role (IRSA, no static AWS keys),
  then presents that AWS identity to GCP's WIF token exchange to get a
  short-lived GCP token impersonating the target service account — no key file
  anywhere in the chain, and the only way to reach BigQuery at all from this
  project's infrastructure (`kind` structurally can't: this org's
  `iam.disableServiceAccountKeyCreation` policy blocks key-based auth, and
  `kind` isn't GKE so there's no Workload Identity path from there either).
- The dbt CronJob's `--target bigquery` run then builds a real mart from *that*
  replicated data — not Milestone 12's synthetic `UNNEST(GENERATE_ARRAY(...))`
  rows. This is what makes BigQuery a genuine warehouse instead of an isolated
  demo: "Kafka for data streaming between systems" and "BigQuery data
  warehouse" become one coherent flow instead of two disconnected checkbox
  items.

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
