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
