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

This is the project's own acceptance bar (`docs/superpowers/specs/2026-09-19-ci-driven-bootstrap-design.md`),
run after any `cluster-up`.

**1. Point `kubectl` at the new cluster and check for unhealthy pods:**
```bash
aws eks update-kubeconfig --name events-api-eks --region eu-central-1 --profile events-api-tf
kubectl get pods -A | grep -vE 'Running|Completed'
```
Expect only the header line — nothing `Pending`/`CrashLoopBackOff`.

**2. Check the 4 Debezium/BigQuery connectors:**
```bash
kubectl get kafkaconnector -n events-api
```
Expect all 4 `READY  True`.

**3. Check data quality (dbt build ran clean):**
```bash
curl -s "http://$(kubectl get ingress events-api -n events-api -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')/health/data-quality" | jq
```
Expect `200` with `passed: true`.

**4. Exercise the app through the ALB — create a tenant, post an event, confirm SSE fan-out:**
```bash
ALB="http://$(kubectl get ingress events-api -n events-api -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"

TENANT_ID=$(curl -s -X POST "${ALB}/admin/tenants" \
  -H "Content-Type: application/json" \
  -d '{"name": "verify-co", "plan_tier": "free"}' | jq -r .id)

# Shell A — stream SSE for that tenant (leave running):
curl -N -H "X-Tenant-ID: ${TENANT_ID}" "${ALB}/stream/events"
```
```bash
# Shell B — post a real event:
curl -X POST "${ALB}/events" \
  -H "Content-Type: application/json" -H "X-Tenant-ID: ${TENANT_ID}" \
  -d '{"event_type": "verify.manual", "user_id": "verify-user", "properties": {}}'
```
Shell A should print the event as a `data: {...}` line within seconds.

**5. Prove independent fan-out (2 pods, not just the Service):** repeat step 4's SSE curl
against each `realtime` pod individually — `kubectl port-forward` to a Service pins to one
pod for the whole session, so this must be pod-to-pod:
```bash
PODS=($(kubectl get pods -n events-api -l app=realtime -o jsonpath='{.items[*].metadata.name}'))
kubectl port-forward -n events-api "${PODS[0]}" 3001:3000 &
kubectl port-forward -n events-api "${PODS[1]}" 3002:3000 &
sleep 2
curl -N -H "X-Tenant-ID: ${TENANT_ID}" http://localhost:3001/stream/events > /tmp/stream1.log &
curl -N -H "X-Tenant-ID: ${TENANT_ID}" http://localhost:3002/stream/events > /tmp/stream2.log &
sleep 2
curl -X POST "${ALB}/events" -H "Content-Type: application/json" -H "X-Tenant-ID: ${TENANT_ID}" \
  -d '{"event_type": "verify.fanout", "user_id": "verify-user", "properties": {}}'
sleep 3
diff /tmp/stream1.log /tmp/stream2.log && echo "IDENTICAL — fan-out confirmed"
kill %1 %2 %3 %4 2>/dev/null
```

**6. Confirm the CDC sinks got the same event** — the consumer's own log line for the Mongo
projection, and a direct BigQuery query:
```bash
kubectl logs -n events-api deploy/cdc-consumer --tail=200 | grep "op=c into Mongo"
```
```bash
bq query --use_legacy_sql=false \
  'SELECT * FROM `events_analytics.events` WHERE user_id = "verify-user" ORDER BY occurred_at DESC LIMIT 1'
```

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
