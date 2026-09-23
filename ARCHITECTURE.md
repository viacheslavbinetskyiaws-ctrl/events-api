# Architecture

What each part of this system is, how it connects to the others, and why it exists.
`CLAUDE.md` covers local dev commands and code-level conventions; this file is the map of the
whole thing — API, data plane, streaming pipeline, analytics, infra, and CI/CD — one level up.

## Data flow, end to end

```
Client
  │
  ▼
ALB  ──/stream/*───────────────▶ realtime (Node, 2 replicas, SSE)
  │                                      ▲
  │ /*                                   │ independent Kafka consumer
  ▼                                      │ group per pod (fan-out, no Redis)
events-api (FastAPI)                     │
  │  RLS-scoped write                    │
  ▼                                      │
Postgres (RDS)                           │
  │  WAL, logical replication            │
  ▼                                      │
Debezium (in Kafka Connect) ──▶ Kafka (Strimzi) ─┼──▶ streaming/ consumer ──▶ MongoDB
                                                   └──▶ BigQuery sink connector ──▶ BigQuery

dbt (hourly CronJob)
  reads Postgres  ──▶ daily_event_counts (Postgres mart)
  reads BigQuery  ──▶ bq_daily_event_counts (BigQuery mart)
  always         ──▶ data_quality_runs row, CloudWatch metric, /health/data-quality
```

One write (`POST /events`) fans out through **three** independent paths — the app's own
Postgres row, a real-time push to any connected client, and two async analytics sinks — without
the API itself knowing any of the last three exist. That decoupling is the point of the whole
CDC layer: `events-api` only ever talks to Postgres.

## Application layer — `app/`

A FastAPI service built ports-and-adapters, with a single composition root (`app/api/deps.py` —
its own docstring states the rule: importing a concrete repository implementation anywhere else
is a dependency-inversion violation). `app/repositories/base.py`/`analytics.py`/
`tenant_accounts.py` define the abstract interfaces; `app/repositories/postgres.py`/`analytics.py`
implement them against Postgres. Routers (`app/api/routers/`) depend only on the abstractions,
never on a concrete class — swapping Postgres for something else later would touch `deps.py` and
nothing in the request-handling code.

**Domain errors, not `HTTPException`, cross the service boundary.** `app/domain/exceptions.py`
defines a `DomainError` hierarchy; `app/services/` raise these, routers let them propagate, and
`app/main.py` registers exception handlers that map each one to an HTTP response. Why: it keeps
`app/services/` free of any HTTP-specific concept, so the same service code could sit behind a
different transport (a CLI, a queue consumer) without modification.

**Multi-tenancy is enforced twice, independently — defense in depth, not either/or.**
`get_tenant_scoped_session` (`deps.py`) sets the Postgres GUC `app.current_tenant` from the
`X-Tenant-ID` header at the start of each request's transaction; a Postgres Row-Level Security
policy reads that GUC and fails closed (zero rows) if it's unset. Repository methods *also*
filter by `tenant_id` explicitly at the app layer. `tenant_accounts` itself is the one table that
opts out — it's the tenant registry, not tenant-owned data.

## Data layer — Postgres (RDS)

**Two roles, deliberately split by privilege.** `events` is the owner role (bypasses RLS, used
by Alembic migrations and dbt for DDL/cross-tenant reads). `events_app` is what the running API
actually connects as — RLS-scoped, granted only what it needs, table by table. Why: if the app's
own credential were ever compromised, it still can't read across tenants or alter schema.

**Two schema owners, one database.** `app/repositories/models.py` has ORM models on two
declarative bases: `Base` for tables Alembic owns (`events`, `tenant_accounts`,
`data_quality_runs`), `DBTBase` for tables dbt owns and materializes (`daily_event_counts`).
`migrations/env.py` filters `DBTBase` tables out of Alembic's autogenerate so it never proposes
dropping something dbt manages. Why two owners instead of one: Alembic's job is the OLTP schema
serving live traffic; dbt's job is rebuilding a derived analytics table from scratch on every
run (`table` materialization — drop and recreate) — those are fundamentally different lifecycles,
and letting one tool own both would mean either Alembic fighting dbt's rebuilds or dbt needing
migration discipline it doesn't have.

## CDC / streaming pipeline

**Why CDC at all, instead of the app writing to Mongo/Kafka/BigQuery directly:** `events-api`
stays a plain CRUD service against one database. Every downstream consumer (audit log, real-time
push, analytics warehouse) reacts to what already happened in Postgres, so adding a fourth sink
later never means touching `app/` again — it means registering another consumer or connector.

- **Debezium** (a Kafka Connect source connector, `k8s/overlays/aws-connect/kafka-connectors.yaml`)
  reads Postgres's write-ahead log via logical replication and publishes one Kafka message per
  row change to `cdc.public.events` / `cdc.public.tenant_accounts`.
- **Kafka**, run by the **Strimzi** operator (`k8s/overlays/aws-cdc/kafka-cluster.yaml`) — the
  broker every consumer below reads from. Strimzi turns "run Kafka on Kubernetes" into a CRD
  (`Kafka`, `KafkaNodePool`) instead of hand-rolled StatefulSets.
- **Kafka Connect** (`k8s/overlays/aws-connect/`) is the plugin host both Debezium (source) and
  the BigQuery sink connector run inside — one JVM process, two connector types, isolated onto
  its own dedicated/tainted node (`terraform/modules/eks`'s `kafka_connect` node pool) so its
  memory footprint never competes with everything else.
- **`streaming/consumer.py`** — a standalone Python process (own `streaming` extra, never
  imported by `app/`) that reads both CDC topics but routes them to two *different* stores,
  dispatched by table name:
  - `events` → **MongoDB** (`streaming/mongo.py`, database `events_projection`, collection
    `event_properties`) — one document per event, `_id` = the event's own id, upserted
    (idempotent by construction: replaying the same document just overwrites it with itself).
    Deletes are mirrored too. Why Mongo specifically for this one: a schemaless projection,
    decoupled from the relational OLTP schema, that can evolve its own document shape without
    an Alembic migration.
  - `tenant_accounts` → a **Postgres** table (`tenant_account_changes`, `TenantAccountChangeORM`)
    — an append-only audit log, deduplicated on `source_lsn` (Debezium's own log sequence
    number) via `ON CONFLICT DO NOTHING`, since a replay needs an explicit dedup key here rather
    than natural upsert idempotency.
- **The BigQuery sink connector** (`com.wepay.kafka.connect.bigquery`) writes *both* topics —
  unlike the Mongo/Postgres split above — into `events_analytics.cdc_events` /
  `cdc_tenant_accounts` in **BigQuery**, the OLAP warehouse dbt's `bigquery/` models build on
  top of (plus the mart they produce, `bq_daily_event_counts`). BigQuery ends up with the
  complete mirror of everything; Mongo and the audit table each get only the one table they're
  actually meant for.

## Real-time layer — `realtime/`

A small Express + kafkajs service (`realtime/src/`) with its own independent Kafka consumer
group per pod (a fresh random group ID at startup) subscribed to the same two CDC topics,
fanning out matching changes as Server-Sent Events on `GET /stream/events`. Why independent
groups instead of a shared one: Kafka only splits partitions *within* one group, so a shared
group would load-balance (each pod sees a subset) — independent groups make every pod see
every message, which is what "broadcast to whoever's connected" actually needs, with zero
Redis pub/sub or other coordination layer.

## Analytics — `dbt/`

Runs against Postgres (source: the app's own `events`/`tenant_accounts` tables) and separately
against BigQuery (source: the CDC sink tables), on an hourly `CronJob`
(`k8s/overlays/aws-dbt/`). `dbt/models/staging/` → `intermediate/` → `marts/` is the standard
staging→mart layering; `dbt/models/bigquery/` mirrors the Postgres mart for the warehouse side.
`dbt/tests/assert_events_recent_volume.sql` is a hand-written data-quality test (not a generic
schema test) asserting the pipeline hasn't gone quiet.

`dbt/scripts/run_and_publish.sh` always runs every step (`dbt build`, `dbt source freshness`,
then `publish_data_quality.py`) and only fails the Job at the end if something genuinely did —
so a real dbt test failure still shows up as a failed Kubernetes Job (useful signal), but never
at the cost of skipping the publish step. `publish_data_quality.py` writes a singleton row to
`data_quality_runs` (deliberately never more than one row — `CHECK (id = 1)`) and pushes a
CloudWatch metric; `app/services/data_quality.py` reads that same table to answer
`GET /health/data-quality` (`503` fail-closed if the latest run failed or none has ever run).
`dbt/scripts/publish_data_quality.py`'s status-merge logic is deliberately *duplicated*, not
imported, from `app/services/data_quality.py` — reusing it would mean the dbt image needs
FastAPI/SQLAlchemy just for ~30 lines; both files flag the duplication so a change to one
doesn't silently drift from the other.

Why dbt owns a Postgres table at all (`daily_event_counts`) instead of only writing to BigQuery:
`GET /analytics/daily` serves it directly from the app's own database — no round-trip to a
separate warehouse for a simple aggregate the app itself exposes.

## Kubernetes — `k8s/`

**A required base plus optional, independently-applicable overlays**, composed with Kustomize.
`k8s/base/` (namespace, Postgres, the app, the migration Job) is what every deployment needs.
Everything else layers on top via `resources: [../base, ...]` (or transitively, via another
overlay that already includes it) and is deliberately optional:

| Overlay | Adds | Needed for |
|---|---|---|
| `aws` | Ingress/ALB, IRSA-annotated ServiceAccount | Any real AWS deployment |
| `aws-cdc` | Kafka (Strimzi), MongoDB (Community Operator) | CDC streaming |
| `aws-connect` | Kafka Connect + Debezium/BigQuery connectors | CDC streaming (needs `aws-cdc` first) |
| `aws-bootstrap` | Suspended one-shot Jobs: RDS IAM grant, migration, role/password setup, publications | Every fresh `cluster-up` |
| `aws-seed` | Initial demo data | Once per cluster lifetime |
| `aws-realtime` | The `realtime` Deployment/Service | SSE fan-out |
| `aws-dbt` | The hourly dbt `CronJob` | Scheduled analytics/data-quality |
| `aws-observability` | kube-prometheus-stack values wiring | Dashboards/metrics |
| `aws-alb-controller` | The AWS Load Balancer Controller's ServiceAccount | Ingress, before the controller's own Helm install |
| `cdc`, `dbt`, `realtime` (no `aws-` prefix) | Same idea, local/kind-cluster variants | Local development |

`k8s/components/cluster-config-*` are Kustomize *Components* (not overlays) that inject
per-run values (account ID, region, RDS host, etc.) via `configMapGenerator` + `replacements` —
generated once per `cluster-up` by `scripts/cluster/render-config.sh`, consumed by every AWS
overlay that needs an environment-specific value.

## Terraform — `terraform/`

**Three roots, split by lifecycle** — the central design decision of the whole infra layer:

| Root | Holds | Lifecycle | Applied by |
|---|---|---|---|
| `terraform/` (foundation) | S3 state bucket, AWS GitHub OIDC roles, budget | Persistent | `terraform.yaml` (`stack: foundation`) |
| `terraform/cluster/` | EKS, RDS, VPC, ECR, the Debezium secret container | Ephemeral — destroyed on `cluster-down` | `cluster-up.yaml`/`cluster-down.yaml`'s `infra` job, or `terraform.yaml` (`stack: cluster`) |
| `terraform/gcp/` | GCP Workload Identity Federation (Kafka Connect's BigQuery auth, GitHub Actions' own GCP auth) | Persistent | Locally only — never in CI, since it provisions the identities the *other* workflows authenticate with |

Why ECR and the Debezium secret live in the *ephemeral* root even though they're not
Kubernetes/EKS resources: everything there gets destroyed and recreated fresh on every
`down`/`up` cycle by design (2026-09-22 cost decision — don't keep paying for images/secrets
between sessions), so they belong with the rest of what's disposable, not with the state
bucket and IAM roles that must survive a teardown.

Each ephemeral resource has a matching identity story: `modules/iam/` wires up IRSA roles
(pod-level AWS permissions via OIDC federation from the EKS cluster's own OIDC provider) so
pods never hold static AWS credentials; `modules/github-oidc/` and `modules/gcp_github_oidc/`
do the same for GitHub Actions itself (AWS and GCP respectively) — one narrowly-scoped role per
CI job (`ecr_push`, `deploy`, `bootstrap`, `terraform_plan`, `terraform_apply`), trusted only for
a `workflow_dispatch`/push on this repo's `main` branch, never a static key anywhere.

## CI/CD — `.github/workflows/` + `scripts/cluster/`

| Workflow | Trigger | Job |
|---|---|---|
| `ci.yaml` | Push/PR (auto) | Unit tests, then (main only) refresh images if a cluster's up; manual `deploy` job force-restarts pods onto a freshly-pushed `:latest` |
| `terraform.yaml` | PR touching `terraform/**` (plan, auto) / dispatch (apply) | One Terraform root at a time, standalone |
| `cluster-up.yaml` | Dispatch only | `infra` → `build-images` → `platform` → `verify-e2e` — the whole disposable stack, from empty AWS account to a verified-working app |
| `cluster-down.yaml` | Dispatch only | Tears the same stack back down, refuses to destroy while an ALB is still attached (would orphan it), asserts nothing billable is left |
| `build-images.yaml` | Reusable only (`workflow_call`) | The shared 5-image build/push matrix `ci.yaml` and `cluster-up.yaml` both call into |

The actual orchestration logic lives in `scripts/cluster/*.sh`
(`platform-up.sh`/`platform-down.sh`/`verify-e2e.sh`, sharing helpers from `lib.sh`), not inline
in the YAML — so the same scripts run identically whether GitHub Actions invokes them or a human
does, on a laptop, for debugging.

`platform-up.sh`'s own last stage is the *fast, non-mutating* half of the acceptance bar (pod
health, connectors, dbt, `/health/data-quality`, ALB reachability) — cheap enough to run on
every single `cluster-up`. `verify-e2e.sh` is the *deep* half (a real tenant, a real event,
2-pod SSE fan-out, both CDC sinks) — it mutates state and takes real time, so it's a separate
job rather than folded into the same stage.

## Observability — kube-prometheus-stack

Prometheus + Grafana (`helm/kube-prometheus-stack/values-override.yaml`), deliberately trimmed
(`nodeExporter` disabled, several components' resource requests right-sized down from chart
defaults) to fit the cluster's own tight node budget — the whole platform runs on `t4g.small`
nodes (2 GiB), and the fixed per-node kubelet/system overhead already eats a meaningful chunk of
that before any workload is scheduled. `topologySpreadConstraints` keep Prometheus and Grafana
off the *same* node as each other; Grafana's `deploymentStrategy: Recreate` (not the chart's
default `RollingUpdate`) avoids needing double memory capacity during its own upgrades. Node
memory pressure on this small a fleet is a recurring, currently-accepted margin rather than a
fully solved problem — `WHATS_NEXT.md` documents several rounds of real, measured capacity
investigations across earlier milestones (JVM heap sizing, operator default requests exceeding
estimates, eviction under real pressure), the same category of issue as the Grafana/Connect
co-location above.

## Why this shape overall

Every major split in this system exists to keep one component's failure or change from forcing
a change somewhere unrelated: two Postgres roles so a compromised app credential can't escalate;
two schema owners so Alembic and dbt never fight each other; CDC instead of direct writes so
`events-api` never needs to know Mongo or BigQuery exist; independent Kafka consumer groups so
`realtime` doesn't need Redis; three Terraform roots so tearing down the expensive, disposable
half never touches the identities and state the next `up` needs to exist in the first place.
