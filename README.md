# events-api

A multi-tenant event-ingestion API with CDC streaming (Kafka/Debezium → Mongo + BigQuery),
dbt-based analytics, and a real-time SSE fan-out layer, deployed on EKS and driven end-to-end
by GitHub Actions. See `CLAUDE.md` for local dev commands and architecture, `AWS_PLAN.md` for
the AWS build-out history, and `WHATS_NEXT.md` for current status.

## Which GitHub Actions workflow do I want?

| I want to... | Use | Trigger |
|---|---|---|
| Bring the whole disposable AWS stack up from nothing | **Cluster up** (`cluster-up.yaml`) | Manual dispatch only |
| Tear the whole disposable AWS stack down to stop paying | **Cluster down** (`cluster-down.yaml`) | Manual dispatch only |
| Push a commit / open a PR, run unit tests | **CI** (`ci.yaml`) → `test` job | Automatic, every push and PR |
| Refresh images in ECR while a cluster happens to already be up | **CI** → `build-push` job | Automatic, every push to `main` (fails loudly if ECR doesn't exist right now — expected if the cluster's down) |
| Force already-running pods to pull the freshly-pushed `:latest` Docker image<br>(a running pod never re-pulls on its own just because a new image was pushed under the same tag) | **CI** → `deploy` job | Manual dispatch |
| Make a small Terraform change without a full up/down cycle | **Terraform** (`terraform.yaml`) | PR touching `terraform/**` → `plan` automatically; manual dispatch (pick `foundation` or `cluster`) → `apply` |
| Re-check a cluster that's already been up a while, without redispatching `cluster-up` | Run `./scripts/cluster/verify-e2e.sh` directly (see below) | Manual, local |

Everything else (`build-images.yaml`) is reusable plumbing, never triggered directly — it's the shared image-build matrix `ci.yaml` and `cluster-up.yaml` both call into, so a change to the build steps only has to be made once.

The one thing to keep straight: **Cluster up/down** own the entire disposable stack (EKS, RDS, ECR, the Debezium secret, the whole Kubernetes platform) — dispatch them for the big lifecycle moves. **Terraform** only ever touches one Terraform root at a time, standalone, for a small infra tweak that doesn't warrant tearing anything down. **CI** never provisions infrastructure at all — it only builds images and, on request, nudges already-running pods to pull a new one.

**Which Terraform root, when dispatching `terraform.yaml`:**

| Root | Holds | Lifecycle |
|---|---|---|
| `terraform/` (`stack: foundation`) | S3 state bucket, GitHub OIDC roles/policies (AWS `github-oidc`), the AWS budget | Persistent — never touched by `cluster-up`/`cluster-down` |
| `terraform/cluster/` (`stack: cluster`) | EKS, RDS, networking/VPC, ECR, the Debezium secret container | Ephemeral — this is exactly what `cluster-up`'s `infra` job applies and `cluster-down`'s `infra` job destroys |
| `terraform/gcp/` | GCP Workload Identity Federation (Kafka Connect's BigQuery auth, GitHub Actions' `verify-e2e` auth) | Persistent, but **not an option in `terraform.yaml` at all** — applied locally only (`terraform -chdir=terraform/gcp apply`), on purpose: this root provisions the identities other workflows authenticate with, so it can never itself depend on CI being reachable |

Picking wrong isn't dangerous (`terraform plan` on the wrong root just shows an unrelated diff, or none), but the rule of thumb: if the change is about *what infrastructure exists while a cluster is up* (an EKS node size, an RDS setting), it's `cluster`; if it's about *identity/access that should survive a `cluster-down`* (a new CI role, a new OIDC trust condition), it's `foundation`; if it's GCP-side IAM/WIF, it's not in this workflow at all — apply `terraform/gcp` from your own machine.

## Running the full application on AWS

Everything AWS-side is disposable and CI/CD-driven — no local Terraform/kubectl/helm commands
are needed to bring it up. Two `workflow_dispatch`-only GitHub Actions workflows own the whole
lifecycle: **Cluster up** and **Cluster down**.

Dispatch from the Actions tab (`Cluster up` → **Run workflow**, branch `main`), or via the CLI:

```bash
gh workflow run cluster-up.yaml --repo viacheslavbinetskyiaws-ctrl/events-api --ref main
gh run watch --repo viacheslavbinetskyiaws-ctrl/events-api
```

This runs four jobs in order: `infra` (Terraform-applies `terraform/cluster` — EKS, RDS, VPC,
ECR, the Debezium secret container), `build-images` (builds and pushes all 5 images into the
now-empty ECR repos), `platform` (`scripts/cluster/platform-up.sh` — deploys Kafka/Strimzi,
Kafka Connect + connectors, MongoDB, the app, `realtime`, the CDC consumer, seeds initial data,
waits for everything to roll out, and runs the fast half of the acceptance bar), then
`verify-e2e` (`scripts/cluster/verify-e2e.sh` — the deep half: a real tenant, a real event,
2-pod SSE fan-out, both CDC sinks; see "Testing the full workflow" below). `infra` has a
90-minute timeout, `platform` 150, `verify-e2e` 30.

The `platform` job's last log line is `cluster-up verification passed (ALB: http://<dns>)` —
that ALB hostname is the app's public entrypoint once the run is green. A green `verify-e2e`
right after it is the real, full proof the run worked end-to-end, not just that things deployed.

## Testing the full workflow

`platform-up.sh`'s own last stage already checks pods, connectors, dbt, `/health/data-quality`
and ALB reachability automatically on every `cluster-up` — that's the fast, non-mutating half of
the project's acceptance bar (`docs/superpowers/specs/2026-09-19-ci-driven-bootstrap-design.md`).

The deeper half — a real tenant, a real event, and proof that fan-out actually reaches two
independent `realtime` pods rather than just one — now runs automatically too, as `cluster-up`'s
`verify-e2e` job. To re-run it manually any time later (e.g. re-checking a cluster that's been up
for a while, with no new dispatch):

```bash
aws eks update-kubeconfig --name events-api-eks --region eu-central-1 --profile events-api-tf
./scripts/cluster/verify-e2e.sh
```

It creates a tenant, posts a real event through the ALB, port-forwards to each `realtime` pod
individually (a `Service` port-forward pins to one pod for the whole session, which wouldn't
prove two *different* pods got the broadcast) and diffs their SSE output, then confirms the same
event landed in both CDC sinks — the `cdc-consumer`'s own Mongo-projection log line, and a row in
`events_analytics.cdc_events` in BigQuery. Any step failing stops the script with that step's
actual error, not a generic "something's wrong."

## Tearing everything down

```bash
gh workflow run cluster-down.yaml --repo viacheslavbinetskyiaws-ctrl/events-api --ref main
gh run watch --repo viacheslavbinetskyiaws-ctrl/events-api
```

Two jobs: `platform` (`scripts/cluster/platform-down.sh` — tears down the Kubernetes side
first, skipped entirely if no cluster exists) then `infra` (refuses to destroy while an ALB
still belongs to the cluster, `terraform destroy`s `terraform/cluster` — EKS, RDS, VPC, ECR,
the Debezium secret — then asserts nothing billable was orphaned: no leftover EBS volumes,
load balancers, NAT gateways, EKS clusters, or RDS instances).

Everything destroyed here is disposable by design — RDS data, ECR images, and the Debezium
password are all recreated/reseeded from scratch on the next `cluster-up`. The persistent
layer (Terraform state bucket, GitHub OIDC roles, IAM policies) is untouched; confirm with:
```bash
terraform -chdir=terraform plan   # expect "No changes."
```

**If the `platform` job fails** (a stuck `Terminating` PVC, a Strimzi finalizer outlasting its
timeout), `infra` is skipped on purpose — destroying with a live ALB would orphan it, so EKS/RDS/
the NAT gateway keep billing until you clear the blocker and re-dispatch **Cluster down**
(`platform-down.sh` is idempotent). As a last resort once the ALB is confirmed gone:
```bash
AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster destroy
```
