# Deploying to kind

## First-time setup

```bash
kind create cluster --config k8s/kind-config.yaml
docker build --target runtime -t events-api:local .
kind load docker-image events-api:local --name events-api
```

Only if you're also bringing up the CDC stack (see below):

```bash
docker build --target runtime-streaming -t events-api-streaming:local .
kind load docker-image events-api-streaming:local --name events-api
```

Note the explicit `--target` on both builds — the `Dockerfile` has four
stages now (`builder`/`runtime` for the app, `builder-streaming`/
`runtime-streaming` for the CDC consumer), and a bare `docker build` with
no `--target` builds whichever stage is physically last in the file, not
necessarily `runtime`.

## Deploy order

Raw manifests have no built-in ordering (no Helm hooks / sync waves here),
so a fresh deploy needs postgres up before the migration job runs, and the
migration job complete before the app starts serving:

```bash
kubectl apply -f k8s/base/namespace.yaml
kubectl apply -f k8s/base/postgres-secret.yaml
kubectl apply -f k8s/base/app-configmap.yaml
kubectl apply -f k8s/base/app-secret.yaml
kubectl apply -f k8s/base/postgres-deployment.yaml
kubectl apply -f k8s/base/postgres-service.yaml
kubectl -n events-api rollout status deployment/postgres --timeout=90s

kubectl apply -f k8s/base/migration-secret.yaml
kubectl apply -f k8s/base/migration-job.yaml
kubectl -n events-api wait --for=condition=complete job/events-api-migrate --timeout=90s

kubectl apply -f k8s/base/deployment.yaml
kubectl apply -f k8s/base/service.yaml
kubectl -n events-api rollout status deployment/events-api --timeout=90s
```

After everything already exists, `kubectl apply -k k8s/base` is fine for
updates — the ordering only matters for a from-scratch deploy.

## CDC stack (optional)

Only needed if you're actually working on streaming/CDC/Mongo — nothing
else in this project depends on it being up. Same "opt-in, not part of
the default deploy" convention docker-compose's own CDC services already
had. Do the app deploy above first (needs `events_app`/RLS/`tenant_accounts`/
`events` to already exist), then:

```bash
kubectl apply -f k8s/overlays/cdc/kafka-service.yaml
kubectl apply -f k8s/overlays/cdc/kafka-statefulset.yaml
kubectl -n events-api rollout status statefulset/kafka --timeout=120s

kubectl apply -f k8s/overlays/cdc/kafka-connect-service.yaml
kubectl apply -f k8s/overlays/cdc/kafka-connect-deployment.yaml
kubectl -n events-api rollout status deployment/kafka-connect --timeout=120s

# Everything else (MongoDB, the consumer, connector registration) — via
# the overlay itself, since the connector-registration Job's ConfigMap
# only gets generated correctly through `-k`, not a plain `-f` on that
# one file. Harmlessly re-applies everything above too (idempotent).
kubectl apply -k k8s/overlays/cdc
kubectl -n events-api rollout status statefulset/mongodb --timeout=90s
kubectl -n events-api wait --for=condition=complete job/cdc-register-connectors --timeout=90s
kubectl -n events-api rollout status deployment/cdc-consumer --timeout=90s
```

Verify a real change actually flows through the whole pipeline (not just
that pods are `Running`):

```bash
kubectl -n events-api port-forward svc/events-api 8000:8000
# in another terminal:
curl -s -X POST localhost:8000/admin/tenants -H "Content-Type: application/json" -d '{"name": "acme", "plan_tier": "starter"}'
# then, with the returned id as X-Tenant-ID:
curl -s -X POST localhost:8000/events -H "Content-Type: application/json" -H "X-Tenant-ID: <id>" -d '{"event_type": "click", "user_id": "user-1", "occurred_at": "2026-08-24T00:00:00Z", "properties": {"page": "/home"}}'
kubectl -n events-api logs deployment/cdc-consumer
```

On a genuinely fresh database (no pre-existing rows), expect the CDC
consumer to take several minutes — not seconds — to pick up newly-created
topics the first time; see `WHATS_NEXT.md`'s migration entry for why
(`confluent-kafka`'s default 5-minute topic-metadata refresh interval).

## dbt scheduling (optional)

Only needed for `GET /analytics/daily` to have real data, and for
`GET /health/data-quality` to work at all in-cluster (both errored/503'd
permanently until this existed — see `WHATS_NEXT.md`). Independent of the
CDC stack above — needs nothing from Kafka/Mongo, only Postgres from
`k8s/base`. Do the app deploy first (needs `events`/`tenant_accounts` to
already exist):

```bash
docker build --target runtime-dbt -t events-api-dbt:local .
kind load docker-image events-api-dbt:local --name events-api
kubectl apply -k k8s/overlays/dbt
```

That creates the `dbt-build` `CronJob` (hourly). To test it immediately
rather than wait for the schedule:

```bash
kubectl -n events-api create job dbt-build-manual --from=cronjob/dbt-build
kubectl -n events-api logs -f job/dbt-build-manual
```

A failing test or stale source is expected to show up as the Job itself
reporting `Failed` (`kubectl -n events-api get jobs`) — that's the
intended signal, not a bug; the result still gets published to
`data_quality_runs` either way, which is what `/health/data-quality`
actually reads.

## Verify

```bash
kubectl -n events-api get pods
kubectl -n events-api port-forward svc/events-api 8000:8000
curl localhost:8000/healthz
curl localhost:8000/readyz
```

## Running dbt against the in-cluster Postgres

dbt isn't deployed into the cluster (see PLAN.md — it runs as its own
pipeline, not inside the API server). To run it against postgres in kind
rather than the docker-compose one, port-forward on a different local
port than 5432 (the docker-compose Postgres already uses that one) and
override the port dbt connects to:

```bash
kubectl -n events-api port-forward svc/postgres 15432:5432
cd dbt && DBT_PORT=15432 uv run dbt build
```

## Notes

- `postgres-deployment.yaml` uses `emptyDir` storage, not a PVC — data is
  wiped if the pod restarts. Deliberate: kind clusters are throwaway for
  this project, and a StatefulSet + PVC would be complexity without a
  payoff here. Don't carry this choice into a real deployment.
- `imagePullPolicy: Never` on both the app Deployment and the migration
  Job assumes the image was loaded via `kind load docker-image` — there's
  no registry involved for local kind testing.
- `migration-secret.yaml`'s `ALEMBIC_DATABASE_URL` is a separate Secret
  from `app-secret.yaml`'s `APP_DATABASE_URL` on purpose, not by
  oversight: the migration Job needs the owner role (DDL rights, bypasses
  RLS), the app needs the restricted `events_app` role. Keeping them in
  separate Secrets means the owner-role credential is only ever injected
  into the migration Job's pod, never the app Deployment's — the app
  should never hold a credential for a role it isn't supposed to use,
  even unused.
- Kubernetes' `command:` overrides Docker's `ENTRYPOINT`, not `CMD` —
  different from docker-compose, where `command:` only overrides `CMD`
  while `ENTRYPOINT` still wraps around it. Bit this project for real:
  `postgres-deployment.yaml`'s original `command: ["postgres", "-c", "wal_level=logical"]` bypassed
  `postgres:17`'s own `docker-entrypoint.sh` (which drops root→`postgres`
  before starting the server) entirely, crash-looping with "root execution
  ... is not permitted." Use `args:` (Kubernetes' equivalent of Docker
  `CMD`) whenever the goal is "pass extra arguments to the image's own
  entrypoint," which is what `postgres-deployment.yaml` now does.
- Kafka's StatefulSet needs `publishNotReadyAddresses: true` on its
  headless governing Service (`kafka-service.yaml`) — KRaft resolves its
  own per-pod hostname (`kafka-0.kafka`) during startup, before it's
  `Ready`, but Kubernetes only publishes a headless Service's per-pod DNS
  records for `Ready` pods by default. Without this, a self-referential
  clustered system can never bootstrap (not `Ready` → no DNS record →
  can't complete its own startup → never `Ready`). Standard requirement
  for any StatefulSet-based clustered workload (Kafka, Cassandra,
  Zookeeper, etc.), not specific to this project.
- Exec-based probes that launch a JVM (like Kafka's
  `kafka-broker-api-versions.sh`) need an explicit `timeoutSeconds` well
  above Kubernetes' own default of `1`s — JVM cold-start alone routinely
  exceeds that, especially under a constrained CPU request.
