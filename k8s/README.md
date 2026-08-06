# Deploying to kind

## First-time setup

```bash
kind create cluster --config k8s/kind-config.yaml
docker build -t events-api:local .
kind load docker-image events-api:local --name events-api
```

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

kubectl apply -f k8s/base/migration-job.yaml
kubectl -n events-api wait --for=condition=complete job/events-api-migrate --timeout=90s

kubectl apply -f k8s/base/deployment.yaml
kubectl apply -f k8s/base/service.yaml
kubectl -n events-api rollout status deployment/events-api --timeout=90s
```

After everything already exists, `kubectl apply -k k8s/base` is fine for
updates — the ordering only matters for a from-scratch deploy.

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
