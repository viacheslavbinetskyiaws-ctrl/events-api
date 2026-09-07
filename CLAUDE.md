# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Read `WHATS_NEXT.md` first for current status and what's actually been verified — it's the living resumption log. `PLAN.md`, `FUTURE_PLAN.md`, and `AWS_PLAN.md` are the three scoping documents (original local-first plan, job-posting gap-closing follow-up, and the not-yet-started real-AWS/EKS phase, respectively) — each has its own Context section explaining what it covers and why it's separate from the others.

## How to work with the user on this project

This is a learning project — the user is preparing for a job interview and is building this hands-on, on purpose. Default to teaching mode for each new milestone/task: explain what needs to change and why, hand over the actual commands/edit content, and let the user run Bash commands and make Write/Edit changes themselves, rather than doing it for them. Check their work afterward (read the file back, verify command output) rather than assuming it's correct. This applies to mutating commands and code edits alike, not just one or the other — and it applies at the start of every new milestone/task by default, not just ones explicitly marked "hands-on." Read-only diagnostics (checking file contents, `kubectl get`/`describe`, `git status`, etc.) are fine to run directly without asking. The user will explicitly hand over execution ("just do it," "go ahead") when they want a different mode for a given stretch of work — don't assume that grant carries forward to unrelated future work.

For symbol-level lookups in Python (`app/`, `streaming/`) or TypeScript (`realtime/`) files — where a function is defined, who calls it, what a type resolves to — prefer the LSP tool (`goToDefinition`/`findReferences`/`hover`/etc.) over grep. Grep is text matching: it false-positives on a comment or string mentioning the name and can't distinguish two same-named symbols in different scopes or an aliased import, where LSP resolves the actual code structure correctly. Grep/Read stay the right call for YAML/Terraform/config/markdown (no language server covers these) or a plain string search where the target isn't a code symbol.

**Do not guess. Check.** Never state a specific expected outcome ("expect `1 to change`", "this shouldn't force replacement", "the property probably isn't gated") unless it's actually been verified against real docs, real schema, or a real prior command output — not plausible-sounding reasoning asserted with confidence. If verifying costs a command, run it. If it can't be verified in advance, say so plainly and let the real output speak, rather than predicting what it will show. A wrong confident guess costs more than an honest "let's see" — it reads as asserted fact and wastes a correction cycle when it's wrong. This has burned real time in this repo already (a Strimzi `/mnt`-path assumption, an Aiven credential-gating assumption, a miscounted Terraform plan) — always check before asserting.

## Commands

Install dependencies (extras are optional workloads, not part of the base API image — see Architecture):
```bash
uv sync --extra dbt --extra streaming --extra mongodb --extra bigquery
```

Run the API locally:
```bash
uv run fastapi dev app/main.py
```

Lint / format:
```bash
uv run ruff check .
uv run ruff format .
```

Tests — unit tests need nothing running; integration tests need `docker compose up -d postgres` first (they run against a separate `events_test` database, created by `docker/init-test-db.sql` on first volume init):
```bash
uv run pytest -m "not integration"   # unit only
uv run pytest -m integration         # integration only
uv run pytest                        # both
```

Migrations (owner role — `ALEMBIC_DATABASE_URL` overrides the target, e.g. for `events_test`):
```bash
uv run alembic upgrade head
uv run alembic revision -m "description"
```

dbt (run from `dbt/`; `DBT_HOST`/`DBT_PORT`/etc. override `profiles.yml`'s connection):
```bash
cd dbt && uv run --extra dbt dbt build
cd dbt && uv run --extra dbt dbt source freshness
```

CDC consumer (standalone process, not run by the API):
```bash
uv run --extra streaming --extra mongodb python -m streaming.consumer
```

Docker images — the `Dockerfile` has four build targets; a bare `docker build` with no `--target` builds whichever is physically last in the file, so always pass one explicitly:
```bash
docker build --target runtime -t events-api:local .
docker build --target runtime-streaming -t events-api-streaming:local .
docker build --target runtime-dbt -t events-api-dbt:local .
```

Kubernetes (kind) — see `k8s/README.md` for the full first-time-setup and deploy-order sequence; the CDC and dbt overlays are optional, kept separate from `k8s/base` deliberately.

## Architecture

**Ports and adapters, enforced by a single composition root.** `app/repositories/base.py`/`analytics.py`/`tenant_accounts.py` define abstract repository interfaces; `app/repositories/postgres.py`/`analytics.py` (the Postgres classes)/`tenant_accounts.py` (the Postgres classes) implement them. `app/api/deps.py` is the *only* place concrete repository/service classes get instantiated and wired to the abstractions the rest of the app depends on — its own docstring states the rule: importing a concrete repository implementation anywhere outside that file is a dependency-inversion violation.

**Domain errors, not `HTTPException`, cross the service boundary.** `app/domain/exceptions.py` defines a `DomainError` hierarchy; routers let these propagate rather than catching them, and `app/main.py` registers FastAPI exception handlers that map each one to an HTTP response. This keeps the service layer free of any HTTP-specific concept.

**Multi-tenancy is enforced twice, independently.** `app/api/deps.py`'s `get_tenant_scoped_session` sets the Postgres GUC `app.current_tenant` (via `set_config`, not string interpolation) from the `X-Tenant-ID` header at the start of each request's transaction; a Postgres RLS policy (`tenant_isolation`, migration `d7a67740cfa5`) reads that GUC and fails closed (zero rows) if it's unset. Repository methods *also* filter by `tenant_id` explicitly at the app layer. Both layers exist on purpose — defense in depth, not either/or. Not every table is tenant-scoped: `tenant_accounts` is the tenant registry itself, not tenant-owned data, so it uses a plain (non-tenant-scoped) session.

**Two Postgres roles, deliberately split by privilege.** `events` is the owner role (superuser, bypasses RLS, used by Alembic migrations and dbt for DDL/cross-tenant reads). `events_app` is what the running API actually connects as — RLS-scoped, granted only what it needs table-by-table (each new table the app touches needs its own explicit `GRANT`). The migration Job and the app Deployment use separate credentials/Secrets in Kubernetes for the same reason — the owner-role credential should never be reachable from the app's own pod, even unused.

**Two schema owners, both present in the same database.** `app/repositories/models.py` has ORM models on two different declarative bases: `Base` for tables Alembic owns and migrates (`events`, `tenant_accounts`, `tenant_account_changes`, `data_quality_runs`), and `DBTBase` for tables dbt owns and materializes (`daily_event_counts`). `migrations/env.py`'s `include_object` filter stops Alembic's autogenerate from proposing drops for `DBTBase`-declared tables it doesn't manage. dbt's own `table` materialization drops and recreates its tables on every run, which wipes any grants/RLS policies applied outside of dbt — `dbt/dbt_project.yml`'s `marts:` config re-applies `+grants` and a `+post-hook` (RLS policy recreation) after every build for exactly this reason.

**`streaming/` and `dbt/` are separate processes, never imported by the API.** Each has its own dependency extra (`streaming`, `mongodb`, `dbt`, `bigquery`) so the API's own Docker image doesn't bundle unrelated dependency trees. `streaming/config.py` has its own `StreamingSettings` rather than reusing `app.core.config.Settings`, for the same reason. `dbt/scripts/publish_data_quality.py` deliberately duplicates (not imports) `app/services/data_quality.py`'s status-merge logic — reusing it would mean the dbt image needs FastAPI/SQLAlchemy/etc. just for ~30 lines; the duplication is flagged in both files' comments so a change to one doesn't silently drift from the other.

**Migration files under `migrations/versions/` are immutable historical records once applied** — `pyproject.toml`'s ruff config excludes that directory from reformatting for this reason. Fixing a genuine bug in an already-applied migration (rather than writing a new one) is only safe here because Alembic tracks applied state by revision ID, not content hash, and every environment this project's migrations have run against is fully within local control (no shared/production database with independently-evolved state).

**Kubernetes: a required base plus optional, independently-applicable overlays.** `k8s/base/` (namespace, Postgres, the app, the migration Job) is what every deployment needs. `k8s/overlays/cdc/` (Kafka, Kafka Connect, MongoDB, the CDC consumer, connector registration) and `k8s/overlays/dbt/` (the scheduled dbt `CronJob`) each layer on top of `k8s/base` via Kustomize but are deliberately optional — skip them unless the work at hand actually needs streaming or scheduled dbt runs. `k8s/overlays/dbt/kustomization.yaml`'s `configMapGenerator` builds its ConfigMap from real files under `scripts/` rather than YAML with inlined script content, and Kustomize auto-rewrites the generated (content-hash-suffixed) name wherever it's referenced.

**RDS IAM auth token lifetime, if working in `terraform/`/AWS-phase code**: a token is only consumed at connection-open time, not for the life of an already-open connection — see `AWS_PLAN.md`'s Milestone 2 for the actual pattern (a SQLAlchemy `creator` callable minting a fresh token per new pooled connection), not a naive "refresh every 15 minutes" implementation.
