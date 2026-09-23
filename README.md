# events-api

A multi-tenant event-ingestion API with CDC streaming (Kafka/Debezium → Mongo + BigQuery),
dbt-based analytics, and a real-time SSE fan-out layer, deployed on EKS and driven end-to-end
by GitHub Actions. See `CLAUDE.md` for local dev commands and architecture, `AWS_PLAN.md` for
the AWS build-out history, and `WHATS_NEXT.md` for current status.

## Running the full application on AWS

Everything AWS-side is disposable and CI/CD-driven — no local Terraform/kubectl/helm commands
are needed to bring it up. Two `workflow_dispatch`-only GitHub Actions workflows own the whole
lifecycle: **Cluster up** and **Cluster down**.

Dispatch from the Actions tab (`Cluster up` → **Run workflow**, branch `main`), or via the CLI:

```bash
gh workflow run cluster-up.yaml --repo viacheslavbinetskyiaws-ctrl/events-api --ref main
gh run watch --repo viacheslavbinetskyiaws-ctrl/events-api
```

This runs three jobs in order: `infra` (Terraform-applies `terraform/cluster` — EKS, RDS, VPC,
ECR, the Debezium secret container), `build-images` (builds and pushes all 5 images into the
now-empty ECR repos), then `platform` (`scripts/cluster/platform-up.sh` — deploys Kafka/Strimzi,
Kafka Connect + connectors, MongoDB, the app, `realtime`, the CDC consumer, seeds initial data,
and waits for everything to roll out). `infra` has a 90-minute timeout, `platform` 150 minutes.

The `platform` job's last log line is `cluster-up verification passed (ALB: http://<dns>)` —
that ALB hostname is the app's public entrypoint once the run is green.

## Testing the full workflow

`platform-up.sh`'s own last stage already checks pods, connectors, dbt, `/health/data-quality`
and ALB reachability automatically on every `cluster-up` — that's the fast, non-mutating half of
the project's acceptance bar (`docs/superpowers/specs/2026-09-19-ci-driven-bootstrap-design.md`).

For the deeper half — a real tenant, a real event, and proof that fan-out actually reaches two
independent `realtime` pods rather than just one — run, any time after a `cluster-up`:

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
