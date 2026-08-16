# Learning Project: FastAPI + dbt + k8s (kind) + Terraform/LocalStack

## Context

The user knows Kubernetes, Terraform, and dbt basics but hasn't combined them into one project, and wants hands-on, interview-relevant "what's actually necessary in production" experience without reading every doc cover to cover. They also want active correction on SOLID/OOP as they write code, since they know the principles but can slip up in practice.

The project is an **event tracking + analytics API**: FastAPI ingests events, dbt transforms them into daily aggregates, FastAPI serves the aggregates back out. This shape (ingest → transform → serve) gives every technology a genuine reason to be there instead of being bolted on.

Sequencing is explicitly local-first: get the app, dbt, and Kubernetes (via a local `kind` cluster) fully working and "production ready" before touching real AWS. A real-AWS/EKS deployment is a deliberately separate follow-up plan, not part of this one.

**Key fact that reshaped scope**: LocalStack restructured pricing in 2026 into a single image with a free "Hobby" tier. Only **EC2/VPC, IAM, and S3** are free. RDS, ECR, ECS, and EKS all now require a paid Base/Ultimate plan. So "test the Terraform locally via LocalStack" can only honestly apply to VPC/IAM/S3 in this phase — RDS/ECR/EKS Terraform modules are deferred to the real-AWS phase, where they can actually be applied and iterated against.

**Local tool check** (already installed): docker 29.6.1, docker compose v5.3.0, kubectl v1.36.1 (bundles Kustomize v5.8.1), terraform v1.15.8, aws-cli 2.32.29, localstack CLI 4.0.3, python 3.14.0, kind (installed manually from the release binary, not brew). `helm` not yet installed — planned for Milestone 6, to be installed when that milestone starts. Installed `dbt-fusion` (Rust preview engine) is **not** what this plan uses — classic `dbt-core` + `dbt-postgres` will be installed in a venv instead, since Fusion is too new/unproven to be what interview questions or most companies' production setups assume.

## Working mode

The user writes the domain/service/repository (business logic) code. Claude scaffolds boilerplate (project structure, Dockerfiles, k8s manifests, Terraform, migrations skeleton) and actively reviews the user's code against SOLID principles and production concerns as it's written — flagging issues rather than silently fixing them. Adjust if a different split is wanted.

## Scope discipline (YAGNI)

App surface stays deliberately small — 3 endpoints only:
- `POST /events` — ingest an event (event_type, user_id, timestamp, properties)
- `GET /events` — list/paginate raw events
- `GET /analytics/daily` — serve the dbt-built daily aggregate

**Explicitly out of scope for this plan (stretch/future, do not build now unless asked):** Ingress, HPA, a full observability stack (Prometheus/Grafana metrics scraping and dashboards), CI/CD pipelines, RBAC beyond defaults, IRSA, ALB ingress controller, ECR/RDS/EKS Terraform modules, real AWS deployment. These belong to the phase-2 (real EKS) plan once this phase is solid. (Helm was originally on this list too, but was pulled back in as Milestone 6 — user explicitly wants hands-on Helm experience, not just the theory. Milestone 9 similarly adds a narrow, deliberately small data-quality/alerting slice — not the full observability stack still deferred here.)

The 3-endpoint core above stays fixed, but Milestones 7–9 (below) each add a small number of individually-justified endpoints (tenant admin CRUD, a data-quality health check) — not a return to scope creep, just growth the core API didn't originally need.

## Milestones

### 0. Scaffolding
- `git init`, base repo layout (`app/`, `dbt/`, `k8s/`, `terraform/`, `tests/`)
- Install `kind`
- Create a Python venv; install `dbt-core` + `dbt-postgres` (not the pre-existing `dbt-fusion`)
- Save this plan as `PLAN.md` at the project root, then proceed to milestone 1.

### 1. FastAPI layered skeleton
- `app/api/` (routers — HTTP concerns only, SRP)
- `app/services/` (business logic, depends on abstractions — DIP)
- `app/repositories/` (abstract repository interface + Postgres implementation — LSP: swappable; ISP: small focused interfaces, not one god-repository)
- `app/domain/` (Pydantic schemas, separate from ORM models)
- `app/core/config.py` using `pydantic-settings` (12-factor env config)
- Structured logging setup, global exception handlers mapping domain errors → consistent HTTP error responses
- `/healthz` and `/readyz` endpoints (used later by k8s probes)
- Claude scaffolds directory structure and stub files; user implements service/repository logic; Claude reviews against SOLID as each piece lands

### 2. Persistence, migrations, dev loop, tests
- SQLAlchemy (async) models + Alembic migrations for the `events` table
- `docker-compose.yml`: Postgres + app, for the fast local dev loop
- pytest: unit tests for services (mock the repository interface — demonstrates DIP paying off), one integration test against a real Postgres (docker-compose test profile)

### 3. dbt project
- `dbt/` project using classic dbt-core against the same Postgres
- `stg_events` (staging: clean/cast raw events)
- `daily_event_counts` mart (aggregate by day + event_type)
- dbt tests: `not_null`, `unique` on key columns
- Wire `GET /analytics/daily` to read from the mart table

### 4. Containerize + kind
- Multi-stage Dockerfile (non-root user, slim base)
- Local `kind` cluster
- Plain k8s YAML manifests organized with Kustomize (no Helm): Namespace, Deployment (resource requests/limits, liveness/readiness probes wired to `/healthz` and `/readyz`), Service, ConfigMap (non-secret config), Secret (DB credentials)
- Deploy to kind, verify the full ingest → dbt → serve flow works end-to-end inside the cluster

### 5. Terraform foundations (LocalStack-validated)
- Provider config supporting both a LocalStack endpoint override (local) and real AWS (later), driven by workspace/variables
- Modules: networking (VPC, subnets, security groups), IAM (roles/policies), S3 (bucket + native S3 state locking, Terraform ≥1.10 supports this without needing DynamoDB)
- Validate these against LocalStack's free Hobby tier (`terraform apply` against the LocalStack endpoint)
- Do **not** write RDS/ECR/EKS modules yet — they require a paid LocalStack tier or real AWS to validate, and untested "production-ready" HCL is worse than no HCL. These are designed in the phase-2 (real EKS) plan, where they can actually be applied and iterated on (noting AWS's own real free-tier RDS — 750 hrs/month `db.t3.micro` for new accounts — as a $0 way to test the real RDS module then).

### 6. Helm (consume + author)

Added after the fact, once Milestone 4 (raw manifests + Kustomize) was done — the user explicitly wants hands-on Helm competence, not just knowing it exists. Deliberately kept in a separate `helm/` setup and a separate namespace from `k8s/base/` (which stays as-is, the raw-manifest reference to compare against), rather than replacing anything already built.

- **Part A — consume a chart**: install the Bitnami/community Postgres chart (`helm repo add`, `helm show values`, `helm install` with an overridden values file, `helm upgrade`, `helm rollback`, `helm uninstall`) — the far more common real-world Helm usage (running someone else's well-maintained chart) than authoring your own.
- **Part B — author a chart**: `helm create` for the starter scaffold, trim it to what's actually needed (YAGNI — delete the ingress/HPA/serviceaccount/tests boilerplate `helm create` generates by default), templatize the already-hand-written `deployment.yaml`/`service.yaml` into `templates/` with a real `values.yaml`, `helm template`/`helm lint`/`helm install --dry-run` before a real install. Use a Helm hook (`helm.sh/hook: pre-install,pre-upgrade`) for the migration Job — this is the direct payoff moment: Milestone 4 needed manual staged `kubectl apply` specifically because raw manifests have no built-in ordering; Helm hooks solve that exact problem natively.

### 7. Multi-tenancy

Added after checking the project against a real senior data engineer job posting — "exposure to multi-tenant systems" was a named requirement, and this milestone maps to it directly.

- Add `tenant_id` to the `events` table via an Alembic migration. Since this is a fresh learning DB there's no real production backfill risk, but the migration is still written the deliberate way: add the column nullable, backfill existing rows to a default tenant, then set `NOT NULL` — demonstrating the pattern rather than skipping straight to a hard `NOT NULL` add.
- Tenant identity comes from a client-supplied `X-Tenant-ID` request header. There is no auth system in this project (out of scope by design — see Scope discipline), so this is a stated simplification: a real system would derive tenant from an authenticated principal (a JWT claim or API key), never trust a client-supplied header directly. Call this out in code comments/README next to the implementation, not just here.
- Repository layer filters every query by `tenant_id`; service layer rejects requests missing the header (400). This is the app-layer half of isolation — belt, not suspenders.
- **Postgres Row-Level Security as the suspenders.** App-layer filtering alone means one repository method that forgets its `WHERE` clause leaks cross-tenant data with no safety net. RLS makes the database itself enforce the boundary: `ALTER TABLE events ENABLE ROW LEVEL SECURITY;`, a policy `USING (tenant_id = current_setting('app.current_tenant')::int)`, and the app sets `SET app.current_tenant = '<id>'` per request/connection (e.g. via a SQLAlchemy session-scoped `event.listens_for` hook right after acquiring a connection). This is what turns "pool" multi-tenancy from a toy pattern into the actual production one — the app is defense in depth, not the only line of defense.
- dbt side (the part most likely to get forgotten): `stg_events` carries `tenant_id` through untouched; the `daily_event_counts` mart's grain changes from `(day, event_type)` to `(day, event_type, tenant_id)`. `GET /analytics/daily` scopes its query by the tenant from the header. Add a `not_null` dbt test on `tenant_id` in both models. dbt's own connection runs as the table owner, which bypasses RLS by default — note this explicitly rather than being surprised when a mart query returns all tenants; either `FORCE ROW LEVEL SECURITY` or accept dbt as a deliberately-privileged batch path is a call to make when building this piece, not now.

### 8. CDC / streaming

**Deliberately docker-compose-only, no kind/k8s deployment for this milestone.** Kafka-on-Kubernetes (StatefulSets, per-broker persistent volumes, cluster coordination) is a real, separate skill that isn't what this milestone is testing — the CDC/streaming pattern itself (log-based replication, decoupled consumers reacting to changes from any writer) is fully demonstrable via docker-compose alone. Same reasoning already used to keep RDS/EKS-Terraform and the real-AWS phase as deliberately separate follow-ups rather than folded in here: if a k8s-deployed Kafka rep is ever wanted, it deserves its own explicit scoping later, not a bolt-on to this milestone.

- New `tenant_accounts` table (id, tenant name, plan tier, created/updated timestamps) — deliberately **not** `events`. CDC on `events` would be redundant with its own dedicated `POST /events` ingestion path and would teach a confusing double-path story. `tenant_accounts` is the genuine CDC case: an operational table mutated by normal CRUD, with something downstream reacting to the changes.
- A small admin endpoint (or two) to create/update tenant accounts — this is the write path CDC captures.
- `wal_level=logical` only needs enabling in `docker-compose.yml`'s postgres service (`command: ["postgres", "-c", "wal_level=logical", ...]`, needs a container restart) — the k8s/Helm Postgres manifests (`k8s/base/postgres-deployment.yaml`, the `events-api-helm` namespace's copy) are deliberately untouched, since nothing from this milestone deploys there.
- Debezium (Postgres CDC connector) + Kafka — or Redpanda, a lighter Kafka-API-compatible alternative that's easier to run locally — added to docker-compose, capturing `tenant_accounts` changes via Postgres logical replication.
- A small Python consumer (`streaming/` or under `app/`) reads the change stream and does something observable: simplest credible option is projecting changes into an audit-log table or structured log output, proving the pipeline works end-to-end without inventing a fake downstream consumer.
- Runs alongside the existing `POST /events` path, not replacing it — demonstrates CDC as an integration pattern (reacting to changes from any writer), which is the actual point interviewers probe.

### 9. Data quality & alerting

New — not in the original scope. Added after comparing the project against a real job posting: multi-tenancy and streaming (Milestones 7–8) were already planned and mapped directly onto named requirements, but "monitoring, alerting, and data-quality checks that let other teams trust the platform without you in the loop" had no counterpart yet, and the posting weighted it heavily (its own bullet, plus a second mention under general requirements). Deliberately kept small — this is not the Prometheus/Grafana stack still listed under Scope discipline as out of scope, just enough to demonstrate the underlying skill:

- Extend the existing dbt tests (`not_null`, `unique`) with a freshness check (`dbt source freshness`, or a custom test) on `events` and `tenant_accounts`.
- Surface dbt test/run failures somewhere visible instead of requiring someone to run `dbt test` manually — smallest credible option: a script or endpoint (e.g. `/health/data-quality`) that reads the latest run's `target/run_results.json` and reports pass/fail, or a webhook notification on failure.
- A row-count/freshness sanity check as a distinct signal from schema-level dbt tests: dbt tests catch structural problems (a null where there shouldn't be one); this catches "the pipeline silently stopped running."

## Production concepts this teaches (mapped to milestones)

| Concept | Milestone |
|---|---|
| Layered architecture / SOLID in practice | 1 |
| 12-factor config, structured logging, error handling | 1 |
| Health/readiness endpoints | 1, 4 |
| Async DB access, schema migrations | 2 |
| Dependency injection via FastAPI `Depends` | 1, 2 |
| Unit vs integration testing, mocking at the right seam | 2 |
| Data quality tests (dbt) | 3 |
| Multi-stage/non-root containers | 4 |
| k8s resource requests/limits, probes, Config/Secret separation | 4 |
| IaC structure, remote state, state locking | 5 |
| LocalStack for infra rehearsal (and its real limits) | 5 |
| Helm authoring (hooks, ordering) vs. consuming a chart | 6 |
| Multi-tenancy: data isolation via request-scoped filtering | 7 |
| dbt mart grain design under multi-tenancy | 7 |
| Change Data Capture (CDC), log-based replication | 8 |
| Event streaming (Kafka/Redpanda), decoupled consumers | 8 |
| Data quality testing, freshness checks, alerting | 9 |

Milestones 10+ (job-posting gap-filling: CDC correctness/replayability, dbt at scale, BigQuery, MongoDB) live in `FUTURE_PLAN.md`, not here — see that file's own concepts table.

## Verification

- Milestone 1–2: `pytest` green locally; manual `curl` against `uvicorn` dev server for all 3 endpoints
- Milestone 3: `dbt run && dbt test` green; `/analytics/daily` returns dbt-built aggregates matching manual SQL spot-check
- Milestone 4: `kind create cluster`, `kubectl apply -k k8s/`, pods `Running` and passing readiness, `kubectl port-forward` + curl full ingest→analytics flow
- Milestone 5: `terraform validate` + `terraform plan`/`apply` against LocalStack endpoint succeed for networking/IAM/S3 modules; `terraform destroy` cleanly tears down
- Milestone 7: `alembic upgrade head` applies cleanly; `pytest` covers cross-tenant isolation (a request scoped to tenant A never returns tenant B's rows); a direct `psql` query with `app.current_tenant` set to tenant A returns zero tenant-B rows even with no `WHERE` clause, proving RLS enforces the boundary independent of app code; `dbt test` passes with the new `tenant_id` `not_null` tests; manual curls with two different `X-Tenant-ID` values return disjoint data
- Milestone 8: `docker compose up` brings up Debezium + Kafka/Redpanda alongside Postgres with `wal_level=logical` confirmed via `SHOW wal_level;`; inserting/updating a row in `tenant_accounts` produces a visible change event in the consumer's output within a few seconds
- Milestone 9: a deliberately-failing dbt test is visibly surfaced (script/endpoint output, or a webhook fires) without manually reading dbt logs; the freshness check correctly flags a table as stale when writes are paused

Milestones 10+ verification criteria live in `FUTURE_PLAN.md`.

## Explicitly deferred to a future, separate plan

Real EKS deployment, ECR/RDS/EKS Terraform modules, Ingress, HPA, a full observability stack (Prometheus/Grafana), CI/CD, IRSA, ALB ingress controller. Job-posting gap-filling (CDC correctness/replayability, dbt at scale, BigQuery, MongoDB, and what was explicitly checked-and-skipped) is its own document — see `FUTURE_PLAN.md`.
