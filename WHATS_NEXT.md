# What's Next

Resumption notes — where things stand, and how to bring everything back up after a Docker restart.

## Current state

- **Milestones 0–4**: done (see `PLAN.md` for full detail).
- **Milestone 5 (Terraform + LocalStack)**: done.
  - Provider config done: `terraform/versions.tf`, `variables.tf`, `providers.tf` — `use_locastack` variable (note: named without the `l` — `use_locastack`, not `use_localstack`; consistent across files so it works, just a naming quirk) drives a `dynamic "endpoints"` block pointing s3/ec2/iam at LocalStack.
  - `modules/networking/` — done, verified against LocalStack directly (VPC, 2 subnets in different AZs, security group).
  - `modules/iam/` — done, verified against LocalStack directly (trust policy, role, logs policy, attachment). Output bug (role_arn pointed at the policy's ARN, not the role's) found and fixed.
  - `modules/s3/` — done: `aws_s3_bucket` (`events-api-tfstate`) + `aws_s3_bucket_versioning` (Enabled) + `aws_s3_bucket_public_access_block` (all 4 flags blocked). No DynamoDB table — locking is handled natively (see backend below).
  - Remote state bootstrap — done: `terraform/backend.tf` holds a `backend "s3"` block (`bucket = "events-api-tfstate"`, `key = "events-api/terraform.tfstate"`, `use_lockfile = true` for native S3 locking). Backend blocks can't reference variables, so unlike `providers.tf` this one is hardcoded to the LocalStack endpoint — swapping to real AWS later means editing this file directly, not flipping `use_locastack`. Migrated via `terraform init -migrate-state`; state now actually lives at `s3://events-api-tfstate/events-api/terraform.tfstate` inside LocalStack. Local `terraform.tfstate` is now just an empty leftover file (harmless, not the source of truth anymore). `use_lockfile` has been exercised via a few `plan`s with no lock-acquisition errors, but hasn't been stress-tested for real concurrent-apply behavior on this LocalStack version.
  - Teardown/verify pass — done: reversed the bootstrap (commented out the `backend "s3"` block in `terraform/backend.tf`, `terraform init -migrate-state` back to local), added `force_destroy = true` to the `aws_s3_bucket.state` resource in `modules/s3/main.tf` (needed since the bucket has versioning enabled — a non-empty/versioned bucket refuses to delete otherwise), then `terraform destroy`. Verified clean directly against LocalStack afterward, not just via `terraform state list`: no buckets, no IAM roles, only LocalStack's own default VPC left. `backend.tf` is currently commented out and `terraform/modules/s3/main.tf` currently has `force_destroy = true` left in — both intentional leftovers from this pass, harmless to leave as-is since there's no live infrastructure right now.
- **Milestone 6 (Helm)**: done.
  - **Part A** — consumed `oci://registry-1.docker.io/bitnamicharts/postgresql` (v18.8.4; Bitnami's classic `charts.bitnami.com` HTTP repo is being phased out in favor of OCI). Installed release `pg-demo` into its own `pg-demo` namespace with `helm/bitnami-postgres/values-override.yaml` (custom `auth.username`/`auth.database`, `primary.persistence.size`). Exercised `helm upgrade` (added `primary.resources`), `helm rollback` (back to revision 1, confirming the chart's `resourcesPreset: nano` default takes over when no explicit override exists), and `helm uninstall`. Confirmed the known StatefulSet gotcha hands-on: the PVC (`data-pg-demo-postgresql-0`) survived `helm uninstall` since `volumeClaimTemplates`-created PVCs aren't Helm-owned — deleted it and the namespace manually afterward. Nothing from Part A is left running.
  - **Part B** — authored `helm/events-api/`, a real chart for the app itself. `helm create events-api`, then deleted the YAGNI boilerplate (`ingress.yaml`, `httproute.yaml` — Gateway API, new default in Helm v4.2.3, `hpa.yaml`, `serviceaccount.yaml`, `tests/`). Templatized `deployment.yaml`/`service.yaml` from `k8s/base` into `values.yaml`-driven equivalents; added `configmap.yaml`/`secret.yaml` (not in the original plan wording, but necessary — the app can't run without its env config). Migration Job (`migration-job.yaml`) uses `helm.sh/hook: pre-install,pre-upgrade` — same ordering problem `k8s/README.md`'s manual staged `kubectl apply` sequence solved, now handled declaratively. Key subtlety: `configmap.yaml`/`secret.yaml` also had to become hooks (`hook-weight: "-5"`, vs the Job's `"0"`) since hooks run *before* a chart's regular resources — otherwise the Job would fire before its own config/secret existed. The Job also needs `hook-delete-policy: before-hook-creation` since Job specs are immutable (breaks a second `helm upgrade` otherwise).
  - Postgres for Part B's chart is **not** part of the chart itself — deliberately reused the raw `k8s/base/postgres-{deployment,service,secret}.yaml` manifests (namespace swapped via `sed` from `events-api` to `events-api-helm`) applied directly with `kubectl`, matching real-world practice where an app's database is usually owned/lifecycled separately from the app's own release (managed service, Operator, or platform team's chart — not a Helm subchart dependency of the app that consumes it).
  - Namespaces in play now: `events-api` (Milestone 4's raw manifests, untouched, still the reference to compare against), `events-api-helm` (Part B's Postgres + the `events-api` Helm release).
- **Milestone 7 (multi-tenancy)**: done, local dev only so far (k8s/AWS credential-split follow-up below still open).
  - `tenant_id` added to `events` via migration `26e3e99a90fe` (nullable → backfill → `NOT NULL`), composite index `(tenant_id, occurred_at)` on `EventORM`. Applied and verified against local dev Postgres.
  - `migrations/env.py` now has `include_object` filtering so autogenerate stops proposing drops for dbt-owned tables (`daily_event_counts`) not in Alembic's `target_metadata` — general fix, not specific to one migration.
  - RLS added via migration `d7a67740cfa5`: new non-owner role `events_app` (grants: `SELECT, INSERT` on `events`, `SELECT` on `daily_event_counts`) + `ALTER TABLE events ENABLE ROW LEVEL SECURITY` + a policy keyed on `current_setting('app.current_tenant', true)`. `events`/Alembic/dbt stay on the owner role (superuser via `POSTGRES_USER`, bypasses RLS unconditionally — needed for dbt's cross-tenant mart aggregation). `app/core/config.py`'s `database_url` default now points at `events_app`; `migrations/env.py`'s default was decoupled from it and hardcoded to the owner role instead (`ALEMBIC_DATABASE_URL` still the override escape hatch).
  - **Known follow-up, not yet done**: `k8s/base/migration-job.yaml` sources its entire env from the same `events-api-secrets`/`events-api-config` the app Deployment uses — no separate `ALEMBIC_DATABASE_URL`. Nothing broken yet since the k8s manifests haven't been touched for the role split at all, but redeploying to kind as-is *after* updating `events-api-secrets`' `APP_DATABASE_URL` to `events_app` would break the migration Job (it'd try to run DDL as a role with no DDL rights). Needs a separate secret/env var for the migration Job pointing at the owner role before this gets redeployed. Same two-credential split will apply later for the real-AWS phase (Secrets Manager/IRSA instead of a k8s Secret, same shape).
  - App-side wiring done and verified end-to-end against the local dev server: `X-Tenant-ID` header extraction (`app/api/deps.py`'s `TenantIdHeader`, typed `UUID | None` — malformed values rejected by FastAPI's own 422, same as a bad `?limit=abc` already was; only "missing entirely" goes through `EventService`'s `DomainError` -> 400, since that's a business rule, not a type constraint), `EventService.ingest`/`list_events` validation, `PostgresEventRepository.list`'s `.where(EventORM.tenant_id == tenant_id)` filter, and `get_tenant_scoped_session` in `deps.py` (the `SET app.current_tenant` hook via `set_config(..., true)`, wired into `EventRepositoryDep` only — `AnalyticsRepositoryDep` stays on the plain session since `daily_event_counts` has no RLS yet). Tested live: no header -> 400, malformed header -> 422, two different tenants each see only their own events via both `POST /events` and `GET /events`.
    - Hit and fixed one real bug along the way: `Header(alias=..., default=None)` inside `Annotated[...]` raises `AssertionError` at FastAPI route-registration time (startup) — the default has to be a plain `= None` on the parameter, not inside `Header()`. Every parameter using `TenantIdHeader` needs its own `= None`.
  - dbt-side grain change done: `stg_events` casts `tenant_id::uuid` through from the source; `daily_event_counts` grain changed from `(event_type, utc_date)` to `(tenant_id, event_type, utc_date)` (select + group by). `_sources.yml`/both `schema.yml`s updated (new source column, `not_null` tests on `tenant_id` in both models, mart's `unique_combination_of_columns` now includes `tenant_id`). `dbt run && dbt test` both green; verified directly via `psql` that tenant A/B each get their own `daily_event_counts` row instead of one merged cross-tenant count.
  - **Real bug caught and fixed**: dbt's `table` materialization drops and recreates the physical `daily_event_counts` object on every `dbt run` — confirmed directly via `\dp`, this silently wiped the `events_app` grant made in migration `d7a67740cfa5` the very next time `dbt run` executed, which would have made `GET /analytics/daily` fail with a permissions error had it not been caught here. Fix lives in `dbt/dbt_project.yml`'s `marts` config, not another migration: `+grants: {select: ["events_app"]}` (dbt-native, re-applied automatically after every rebuild) and a `+post-hook` that re-enables RLS + recreates the `tenant_isolation` policy (`FOR SELECT` only — `events_app` has no write grant on this table) after every build, for the same reason (policies are attached to the table object and vanish with it).
  - `DailyEventCountORM` (`app/repositories/models.py`) needed `tenant_id` added as part of its composite primary key too — missed in the first pass since it's a separate, dbt-owned read-shape declaration, not something that follows automatically from the dbt model/schema.yml changes.
  - `GET /analytics/daily` now fully tenant-scoped: same shape as `events` — `AnalyticsService.get_daily_counts` rejects a missing tenant with `DomainError` -> 400, `PostgresAnalyticsRepository.get_daily_counts` filters by it, and `get_analytics_repository` in `deps.py` switched from the plain session to `TenantScopedSessionDep` so the `app.current_tenant` hook actually fires for this endpoint too. Verified live: no header -> 400, tenant A/B each get only their own row.

## Bringing everything back up after a Docker restart

```bash
# 1. Start Docker Desktop itself first (GUI), then:

# 2. docker-compose Postgres (local dev + pytest databases)
cd /Users/vyacheslavbinetsky/Develop/learn/k8s-tf-dbt
docker compose up -d postgres

# 3. kind cluster — check status before assuming anything's broken
kind get clusters
kubectl cluster-info --context kind-events-api
kubectl -n events-api get pods

# If the node/pods aren't healthy after Docker restarts (kind's node is
# itself a Docker container, and stopped containers don't always resume
# cleanly), the reliable fallback is just rebuilding from what's already
# written — nothing about this cluster is precious:
kind delete cluster --name events-api
kind create cluster --config k8s/kind-config.yaml
# then reapply in order per k8s/README.md (postgres -> wait -> migration job -> wait -> app)

# 4. LocalStack — --persist is no longer optional, see caveat below
lstk start --persist
```

(Note: this `lstk` version, 0.18.0, has no `-d`/detach flag — `lstk start --help` to confirm if a newer version changes this.)

**LocalStack caveat**: LocalStack state does *not* persist across restarts unless started with `--persist`. Right now (post Milestone 5 teardown) there's nothing live to lose — Terraform's state is back to local (currently empty, `backend "s3"` is commented out in `terraform/backend.tf`), so a restart without `--persist` just means the default LocalStack VPC comes back empty, no drift risk.

This becomes higher-stakes again the moment remote state gets re-bootstrapped (uncommenting `backend "s3"` + `terraform init -migrate-state`, e.g. if this project revisits it or the phase-2 real-AWS plan does something similar): once Terraform's own state file lives *inside* a LocalStack-hosted bucket, starting LocalStack without `--persist` wipes the state file itself, not just the resources — worse than the pre-Milestone-5 situation since there'd be no local fallback copy. Use `lstk start --persist` whenever a remote backend is active.

## Docker Desktop resource limits

Checked actual usage before suggesting anything — current combined usage across all three (LocalStack, kind, Postgres) is about **1.6GB**, against an 8GB allocation, on a 24GB/12-core Mac. That's not remotely a bottleneck; no change is actually necessary. If you want to be more conservative to leave more headroom for the rest of the system:
- **RAM**: 8GB is already generous (5x actual usage) — fine as-is, or could even drop to 4–6GB if you want more free for other apps, but there's no problem to fix here.
- **CPU**: "unlimited" lets the Docker VM use all 12 cores if something spikes. Nothing we run is CPU-heavy enough to need that, so capping it at something like 6–8 would leave more consistent headroom for the host without affecting anything we're doing — optional, not a fix for an actual problem.
- **Swap (1GB)** and **disk (unlimited)**: both fine, no evidence of pressure on either.
