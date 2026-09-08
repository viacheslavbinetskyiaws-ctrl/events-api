# AWS Milestone 6 (Observability: Prometheus/Grafana + CloudWatch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work.

**Goal:** Get a real Grafana dashboard showing live request-rate/latency for
the FastAPI app under actual traffic (via `kube-prometheus-stack` +
`prometheus-fastapi-instrumentator`), and a real CloudWatch Alarm + SNS
notification that fires when the dbt CronJob's build fails — two different
tools for two genuinely different signal types, not redundant tooling.

**Architecture:** `kube-prometheus-stack` installed via `helm install`
(matching this project's existing raw-Helm precedent — no Helm/Kubernetes
Terraform provider anywhere in this repo), trimmed to fit this cluster's
actual remaining capacity. The app gets a one-line instrumentation addition
exposing `/metrics`, scraped via a `ServiceMonitor`. Separately — and this is
real, necessary scope this milestone absorbs, not something already sitting
there — the dbt CronJob has never run against RDS at all (only a kind-only
overlay exists), so it gets its own IRSA role (mirroring the migration Job's
existing pattern) and its own AWS overlay before it can be alarmed on. Its
own container pushes a custom CloudWatch metric after every run; a
`aws_cloudwatch_metric_alarm` on that metric drives an SNS email
notification.

**Tech Stack:** `kube-prometheus-stack` 90.0.0 (Prometheus Operator,
Prometheus, Grafana, Alertmanager, kube-state-metrics), `prometheus-fastapi-instrumentator`
8.1.0, `aws_cloudwatch_metric_alarm`/`aws_sns_topic`/`aws_sns_topic_subscription`
(AWS provider ~> 6.62.0, verified against current registry docs this
session), boto3 (already a base dependency).

**Spec:** `AWS_PLAN.md`'s "6. Observability: Prometheus/Grafana + CloudWatch,
each doing what it's actually for" section, plus this session's brainstorming
conversation (capacity trade-off analysis, dbt-on-AWS gap discovery, Terraform
syntax verified live against the registry, chart defaults verified live via
`helm show values` rather than assumed) — this plan argues from both.

## Global Constraints

- `AWS_PROFILE=events-api-tf` for every AWS CLI/Terraform call. Region
  `eu-central-1`, account `938500344309`.
- Every image build targets **`--platform linux/arm64`** — the whole cluster
  is `t4g.small`/Graviton (`terraform/modules/eks/main.tf`'s
  `ami_type = "AL2023_ARM_64_STANDARD"`). Milestone 5's migration-IRSA
  follow-up hit exactly this bug once already (a wrong-platform rebuild
  silently produced a pod that couldn't run) — don't repeat it.
- Nodes are already tight: `ip-10-0-11-186` sits at 97% memory-requested
  (~41Mi headroom), `ip-10-0-11-18` at 82%. Confirmed live this session via
  `kubectl describe nodes`, not assumed. `kube-prometheus-stack` gets
  `nodeExporter.enabled: false` — it's the one DaemonSet component (must
  schedule on *every* untainted node, no scheduler choice) whose data
  (host-OS-level EC2 stats) nothing in this project's scope reads.
  `kubeStateMetrics` stays on (single Deployment pod, scheduler picks where —
  and its `kube_deployment_status_replicas` metric is what Milestone 7's
  Grafana bonus needs). Every remaining component (`prometheus`, `grafana`,
  `alertmanager`, `kube-state-metrics`) gets an **explicit** modest
  `resources.requests/limits` — an unset request is what let a real pod get
  evicted in Milestone 3 (Kafka Connect); don't repeat that either.
- `serviceMonitorSelectorNilUsesHelmValues: true` (confirmed live via
  `helm show values`, not assumed) means Prometheus only picks up a
  `ServiceMonitor` carrying label `release: kube-prometheus-stack` — that's
  the Helm release name Task 3 installs under. Get this label wrong and the
  ServiceMonitor silently does nothing (no error anywhere).
  `serviceMonitorNamespaceSelector: {}` does mean all namespaces, though, so
  the ServiceMonitor can live in `events-api` directly, next to the Service.
- Grafana's admin password: leave `adminPassword` unset entirely (don't
  invent a plaintext credential to commit) — the chart auto-generates one
  into Secret `kube-prometheus-stack-grafana` (key `admin-password`), same
  "no committed credentials" discipline this repo already holds everywhere
  else.
- `prometheus-fastapi-instrumentator`'s default metrics (confirmed against
  its current source, not memory): `http_requests_total{method,status,handler}`,
  `http_request_duration_seconds{method,handler}` (few buckets), and
  `http_request_duration_highr_seconds` (no labels, many buckets — built
  specifically for `histogram_quantile`, use this one for the latency panel).
- The dbt CronJob has **never run against RDS** — `k8s/overlays/dbt/` is
  kind-only (`DBT_HOST: postgres`, `image: events-api-dbt:local`), and the
  `events-api-dbt` ECR repo is confirmed empty (`aws ecr describe-images`
  returned `[]` this session). Porting it is real prerequisite work, not
  optional polish.
- dbt connects as the **owner role `events`** (needs DDL for marts), not
  `events_app` — same role the migration Job's IRSA already uses, but a
  *separate* IRSA role/ServiceAccount pair (`events-api-dbt`), matching this
  repo's established "each workload gets its own identity even when sharing
  a DB role" convention (`CLAUDE.md`'s migration-Job-vs-app-Deployment
  credential split).
- `cloudwatch:PutMetricData` has no resource-level IAM scoping — `Resource: "*"`
  is a documented AWS constraint on that specific action, not a design gap.
- SNS email subscription reuses `var.budget_notification_email`'s existing
  value (same person already gets budget alerts) — no new Terraform variable.
- Follow `AWS_PLAN.md`'s existing per-milestone teardown discipline at the
  end: `-target=` destroy for `module.eks`/`module.rds`/`module.networking`
  plus this milestone's new `dbt_irsa` role and the root-level SNS/alarm
  resources; `module.s3`/`module.ecr`/the base `module.iam` roles stay
  permanent as before.
- Don't run `git commit` unless explicitly asked in that turn — Task 10
  hands over the command, doesn't run it. No task before Task 10 commits
  anything.

---

### Task 1: Instrument the FastAPI app with `prometheus-fastapi-instrumentator`

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/main.py`

**Interfaces:**
- Produces: a `/metrics` endpoint on the running app, in Prometheus
  exposition format. Task 4's `ServiceMonitor` scrapes this path.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`'s base `dependencies` list (same reasoning as `boto3` —
this runs in every environment, kind and AWS both, so it's a base dependency,
not an extra):

```toml
dependencies = [
    "alembic>=1.18.5",
    "asyncpg>=0.31.0",
    "boto3>=1.43.88",
    "fastapi-swagger-dark>=0.0.9",
    "fastapi[standard-no-fastapi-cloud-cli]>=0.139.2",
    "prometheus-fastapi-instrumentator>=8.1.0",
    "pydantic-settings>=2.14.2",
    "sqlalchemy[asyncio]>=2.0.51",
]
```

- [ ] **Step 2: Sync**

```bash
uv sync --extra dbt --extra streaming --extra mongodb --extra bigquery
```

- [ ] **Step 3: Instrument the app**

In `app/main.py`, add the import alongside the existing ones and call it
right after the routers are registered:

```python
from prometheus_fastapi_instrumentator import Instrumentator
```

```python
app.include_router(health.router)
app.include_router(events.router)
app.include_router(analytics.router)
app.include_router(admin.router)

Instrumentator().instrument(app).expose(app)
```

(Placed after `include_router` calls so instrumentation wraps every route
already registered above it — matches the library's own documented ordering.)

- [ ] **Step 4: Verify locally**

```bash
uv run fastapi dev app/main.py
```

In another terminal:

```bash
curl -s http://localhost:8000/metrics | head -30
```

Expected: real Prometheus exposition text, including lines starting
`# HELP http_requests_total` and `# TYPE http_requests_total counter`. Hit
`GET /health` a couple of times first, then re-curl `/metrics` and confirm
`http_requests_total{...,handler="/health",...}` shows a nonzero count —
confirms the metric actually increments, not just that the endpoint exists.

---

### Task 2: Make the app's Service scrapeable, deploy the instrumented app to EKS

**Files:**
- Modify: `k8s/base/service.yaml`

**Interfaces:**
- Consumes: Task 1's `/metrics` endpoint (must be running in the deployed
  image for this task's live verification to mean anything).
- Produces: the `events-api` Service now carries `metadata.labels: {app:
  events-api}` and a named port `http` — Task 4's `ServiceMonitor` selects
  on the Service's labels and references the port by name, not number.

- [ ] **Step 1: Edit the Service**

`k8s/base/service.yaml` currently has no `metadata.labels` and an unnamed
port. Change it to:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: events-api
  namespace: events-api
  labels:
    app: events-api
spec:
  selector:
    app: events-api
  ports:
    - name: http
      port: 8000
      targetPort: 8000
```

This is a backward-compatible addition (a label and a port name don't change
routing behavior) — safe for the kind deployment too, not just AWS.

- [ ] **Step 2: Dry-run render before touching the cluster**

```bash
kubectl kustomize k8s/overlays/aws | grep -A8 "kind: Service"
```

Expected: the `events-api` Service shows `labels: {app: events-api}` and
`- name: http` under `ports`.

- [ ] **Step 3: Rebuild and push the app image — arm64**

```bash
docker build --platform linux/arm64 --target runtime -t events-api:local .
AWS_PROFILE=events-api-tf aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin 938500344309.dkr.ecr.eu-central-1.amazonaws.com
docker tag events-api:local 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-app:latest
docker push 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-app:latest
```

- [ ] **Step 4: Apply and roll out**

```bash
export KUBECONFIG=~/.kube/config
AWS_PROFILE=events-api-tf aws eks update-kubeconfig --name events-api-eks --region eu-central-1
kubectl apply -k k8s/overlays/aws
kubectl -n events-api rollout restart deployment/events-api
kubectl -n events-api rollout status deployment/events-api
```

- [ ] **Step 5: Verify `/metrics` on the live pod**

```bash
kubectl -n events-api port-forward svc/events-api 8000:8000
```

In another terminal:

```bash
curl -s http://localhost:8000/metrics | grep http_requests_total | head -5
```

Expected: real metric lines, same shape as Task 1's local check, now coming
from the actual deployed pod.

---

### Task 3: Install `kube-prometheus-stack` via Helm, trimmed to fit

**Files:**
- Create: `helm/kube-prometheus-stack/values-override.yaml`

**Interfaces:**
- Produces: a running Prometheus + Grafana + Alertmanager +
  kube-state-metrics in namespace `monitoring`, Helm release name
  `kube-prometheus-stack` (Task 4's `ServiceMonitor` label depends on this
  exact release name).

- [ ] **Step 1: Add the chart repo**

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update prometheus-community
```

- [ ] **Step 2: Write the values override**

`helm/kube-prometheus-stack/values-override.yaml` (mirrors
`helm/bitnami-postgres/values-override.yaml`'s existing convention of
committing the real values file used):

```yaml
# Trimmed for this cluster's actual remaining capacity (nodes confirmed live
# at 82-97% memory-requested before this install). nodeExporter is the one
# component cut entirely — it's a DaemonSet (must schedule on every
# untainted node, no scheduler choice) exporting host-OS-level EC2 stats
# nothing in this project reads. kubeStateMetrics stays: it's a single
# Deployment pod the scheduler can place anywhere, and its
# kube_deployment_status_replicas metric is what feeds this milestone's
# Grafana dashboard (and Milestone 7's HPA-replica-count bonus later).
nodeExporter:
  enabled: false

alertmanager:
  alertmanagerSpec:
    resources:
      requests:
        cpu: 25m
        memory: 64Mi
      limits:
        cpu: 100m
        memory: 128Mi

prometheus:
  prometheusSpec:
    # No storageSpec — ephemeral, matches this project's per-milestone
    # destroy discipline. Nothing here needs to survive a terraform destroy.
    retention: 6h
    resources:
      requests:
        cpu: 100m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi

grafana:
  # adminPassword deliberately left unset — the chart auto-generates one
  # into Secret kube-prometheus-stack-grafana (key admin-password). No
  # plaintext credential committed here.
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

kube-state-metrics:
  resources:
    requests:
      cpu: 25m
      memory: 64Mi
    limits:
      cpu: 100m
      memory: 128Mi
```

- [ ] **Step 3: Install**

```bash
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --version 90.0.0 \
  -f helm/kube-prometheus-stack/values-override.yaml
```

- [ ] **Step 4: Verify everything actually scheduled — this is the real test of the capacity call**

```bash
kubectl -n monitoring get pods -o wide
```

Expected: every pod `Running`/`Ready`, spread across whichever nodes had
room — not `Pending` with a `FailedScheduling` event. If anything is
`Pending`:

```bash
kubectl -n monitoring describe pod <pending-pod-name> | tail -15
```

Read the actual event before changing anything — don't guess which resource
request is the culprit.

---

### Task 4: `ServiceMonitor` for the app

**Files:**
- Create: `k8s/overlays/aws-observability/kustomization.yaml`
- Create: `k8s/overlays/aws-observability/service-monitor.yaml`

**Interfaces:**
- Consumes: Task 2's `events-api` Service (`labels: {app: events-api}`, port
  name `http`) and Task 3's running Prometheus Operator (CRD
  `ServiceMonitor` must already exist in the cluster).
- Produces: a live Prometheus scrape target for the app.

- [ ] **Step 1: Write the kustomization**

`k8s/overlays/aws-observability/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - service-monitor.yaml
```

- [ ] **Step 2: Write the ServiceMonitor**

`k8s/overlays/aws-observability/service-monitor.yaml` — the `release: kube-prometheus-stack`
label is load-bearing (see Global Constraints — without it, Prometheus's
`serviceMonitorSelectorNilUsesHelmValues` behavior means this is silently
never picked up):

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: events-api
  namespace: events-api
  labels:
    release: kube-prometheus-stack
spec:
  selector:
    matchLabels:
      app: events-api
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
```

- [ ] **Step 3: Apply and verify the target is actually being scraped**

```bash
kubectl apply -k k8s/overlays/aws-observability
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

In another terminal:

```bash
curl -s 'http://localhost:9090/api/v1/targets' | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    if 'events-api' in t['labels'].get('job', ''):
        print(t['labels'], t['health'])
"
```

Expected: one line with `health: up`. If nothing prints, the ServiceMonitor
either isn't labeled correctly or the Service selector doesn't match — check
`kubectl get servicemonitor -n events-api events-api -o yaml` against
`kubectl get svc -n events-api events-api --show-labels` before assuming
anything else is wrong.

---

### Task 5: Grafana dashboard, provisioned as a ConfigMap, verified against real traffic

**Files:**
- Create: `k8s/overlays/aws-observability/grafana-dashboard-configmap.yaml`
- Modify: `k8s/overlays/aws-observability/kustomization.yaml`

**Interfaces:**
- Consumes: Task 4's live Prometheus target (`http_requests_total`,
  `http_request_duration_highr_seconds`) and `kube-state-metrics`'
  `kube_deployment_status_replicas` (from Task 3).

- [ ] **Step 1: Write the dashboard JSON as a labeled ConfigMap**

Grafana's chart runs a sidecar (`sidecar.dashboards.enabled: true` by
default, confirmed via `helm show values`) that auto-loads any ConfigMap
anywhere in the cluster labeled `grafana_dashboard: "1"` — no Helm values
change needed, just the labeled ConfigMap itself.

`k8s/overlays/aws-observability/grafana-dashboard-configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: events-api-dashboard
  namespace: events-api
  labels:
    grafana_dashboard: "1"
data:
  events-api.json: |
    {
      "title": "events-api",
      "uid": "events-api",
      "timezone": "browser",
      "refresh": "10s",
      "time": { "from": "now-15m", "to": "now" },
      "panels": [
        {
          "id": 1,
          "title": "Request rate",
          "type": "timeseries",
          "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
          "targets": [
            {
              "expr": "sum(rate(http_requests_total[1m]))",
              "legendFormat": "requests/sec"
            }
          ]
        },
        {
          "id": 2,
          "title": "p95 latency",
          "type": "timeseries",
          "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
          "targets": [
            {
              "expr": "histogram_quantile(0.95, sum(rate(http_request_duration_highr_seconds_bucket[5m])) by (le))",
              "legendFormat": "p95 seconds"
            }
          ]
        },
        {
          "id": 3,
          "title": "events-api replica count",
          "type": "timeseries",
          "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
          "targets": [
            {
              "expr": "kube_deployment_status_replicas{deployment=\"events-api\", namespace=\"events-api\"}",
              "legendFormat": "replicas"
            }
          ]
        }
      ]
    }
```

- [ ] **Step 2: Add it to the overlay**

`k8s/overlays/aws-observability/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - service-monitor.yaml
  - grafana-dashboard-configmap.yaml
```

- [ ] **Step 3: Apply, log into Grafana, get the auto-generated password**

```bash
kubectl apply -k k8s/overlays/aws-observability
kubectl -n monitoring get secret kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

Open `http://localhost:3000`, log in as `admin` with that password, confirm
the "events-api" dashboard exists (Dashboards → browse) with three panels,
currently flat/near-zero.

- [ ] **Step 4: Drive real traffic and watch it move — the actual verification bar for this milestone**

In another terminal, keep the app's own port-forward running (Task 2, Step
5) and generate a real burst:

```bash
for i in $(seq 1 200); do curl -s -o /dev/null http://localhost:8000/health; done
```

Watch the Grafana dashboard (refresh is 10s) — expected: the request-rate
panel visibly spikes, then decays back down within ~1 minute of the burst
ending; the p95 latency panel shows a nonzero value during the burst.

- [ ] **Step 5: Capture this visually**

Per this milestone's own verification bar (`AWS_PLAN.md`: "a real Grafana
dashboard shows live request-rate/latency panels during an actual `curl`
burst"), take a screenshot of the dashboard mid-burst as durable proof, not
just an in-session visual check — use the browser automation tooling
available in this environment (Claude in Chrome or Playwright) to navigate
to `http://localhost:3000`, log in, open the dashboard, and screenshot it
while Step 4's curl loop is running.

---

### Task 6: Dedicated IRSA role for the dbt CronJob (Terraform)

**Files:**
- Modify: `terraform/modules/iam/main.tf`
- Modify: `terraform/modules/iam/outputs.tf`
- Modify: `terraform/outputs.tf`

**Interfaces:**
- Consumes: `var.oidc_provider_arn`/`var.oidc_provider_url` (already passed
  into this module), `var.rds_resource_id` (already passed in).
- Produces: `aws_iam_role.dbt_irsa`, whose ARN Task 7's ServiceAccount
  annotation uses.

- [ ] **Step 1: Add the trust policy, role, and both policies**

In `terraform/modules/iam/main.tf`, add after the existing `migration_irsa`
block (mirrors it exactly, but scoped to a different ServiceAccount and with
a second policy attached):

```hcl
data "aws_iam_policy_document" "dbt_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-api-dbt"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dbt_irsa" {
  name               = "${var.name_prefix}-dbt-irsa"
  assume_role_policy = data.aws_iam_policy_document.dbt_irsa_trust.json
}

data "aws_iam_policy_document" "dbt_rds_connect" {
  statement {
    actions   = ["rds-db:connect"]
    resources = ["arn:aws:rds-db:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:dbuser:${var.rds_resource_id}/events"]
  }
}

resource "aws_iam_policy" "dbt_rds_connect" {
  name   = "${var.name_prefix}-dbt-rds-connect"
  policy = data.aws_iam_policy_document.dbt_rds_connect.json
}

resource "aws_iam_role_policy_attachment" "dbt_irsa_rds" {
  role       = aws_iam_role.dbt_irsa.name
  policy_arn = aws_iam_policy.dbt_rds_connect.arn
}

# cloudwatch:PutMetricData has no resource-level scoping in IAM — Resource
# "*" is a documented AWS constraint on this specific action, not a design
# gap.
data "aws_iam_policy_document" "dbt_cloudwatch_put_metric" {
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "dbt_cloudwatch_put_metric" {
  name   = "${var.name_prefix}-dbt-cloudwatch-put-metric"
  policy = data.aws_iam_policy_document.dbt_cloudwatch_put_metric.json
}

resource "aws_iam_role_policy_attachment" "dbt_irsa_cloudwatch" {
  role       = aws_iam_role.dbt_irsa.name
  policy_arn = aws_iam_policy.dbt_cloudwatch_put_metric.arn
}
```

- [ ] **Step 2: Output the role ARN**

`terraform/modules/iam/outputs.tf`:

```hcl
output "dbt_irsa_role_arn" {
  description = "IRSA role ARN for the dbt CronJob's IAM auth as the owner role, plus CloudWatch metric push"
  value       = aws_iam_role.dbt_irsa.arn
}
```

Root `terraform/outputs.tf`:

```hcl
output "dbt_irsa_role_arn" {
  description = "IRSA role ARN for the dbt CronJob's IAM auth as the owner role, plus CloudWatch metric push"
  value       = module.iam.dbt_irsa_role_arn
}
```

- [ ] **Step 3: Plan, review, apply**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform plan
```

Expect additions for: `aws_iam_role.dbt_irsa`, two `aws_iam_policy`
resources, two `aws_iam_role_policy_attachment` resources, plus the two new
`data` sources (data sources don't count toward the add total). Read the
actual printed count before applying rather than assuming a number.

```bash
AWS_PROFILE=events-api-tf terraform apply
```

- [ ] **Step 4: Get the real role ARN for Task 7**

```bash
terraform output -raw dbt_irsa_role_arn
```

---

### Task 7: Port the dbt CronJob to AWS, verify a real successful run against RDS

**Files:**
- Create: `k8s/overlays/aws-dbt/kustomization.yaml`
- Create: `k8s/overlays/aws-dbt/service-account.yaml`
- Create: `k8s/overlays/aws-dbt/dbt-cronjob.yaml`
- Create: `dbt/scripts/generate_db_token.py`
- Modify: `dbt/profiles.yml`
- Modify: `dbt/scripts/run_and_publish.sh`

**Interfaces:**
- Consumes: Task 6's `dbt_irsa_role_arn`.
- Produces: a real, passing `dbt-build` CronJob running against RDS — Task 8
  builds the CloudWatch signal on top of this.

**Note on file layout:** this project's existing `aws-cdc`/`aws-realtime`
overlays each hold their own full copy of the environment-specific resources
(layered only on `../aws`), rather than patching a shared kind-only overlay
file — checked live via `cat k8s/overlays/aws-cdc/kustomization.yaml` this
session. `aws-dbt` follows that same precedent: a standalone
`dbt-cronjob.yaml`, not a Kustomize patch on top of `../dbt`'s.

- [ ] **Step 1: `sslmode` in the dbt profile**

`dbt/profiles.yml` — add `sslmode` to the `dev` output, defaulting to
`prefer` (harmless everywhere else, matches RDS IAM auth's requirement of
`require` when actually on AWS — confirmed `sslmode` is a real dbt-postgres
profile field against current dbt docs this session):

```yaml
events_analytics:
  target: dev
  outputs:
    dev:
      type: postgres
      host: "{{ env_var('DBT_HOST', 'localhost') }}"
      port: "{{ env_var('DBT_PORT', '5432') | as_number }}"
      user: "{{ env_var('DBT_USER', 'events') }}"
      password: "{{ env_var('DBT_PASSWORD', 'events') }}"
      dbname: "{{ env_var('DBT_DBNAME', 'events') }}"
      sslmode: "{{ env_var('DBT_SSLMODE', 'prefer') }}"
      schema: public
      threads: 4
```

- [ ] **Step 2: Token-minting script**

`dbt/scripts/generate_db_token.py` (boto3 is already a base dependency, no
new install needed — same `generate_db_auth_token` call
`migrations/env.py` already uses, exact keyword args already confirmed
against a real `help()` output in Milestone 2):

```python
"""Mints a fresh RDS IAM auth token for the dbt CronJob's connection, printed
to stdout so run_and_publish.sh can capture it into DBT_PASSWORD. One token
per run is fine here — unlike the app's long-lived connection pool, dbt
build finishes well inside the token's 15-minute lifetime.
"""

import os

import boto3


def main() -> None:
    client = boto3.client("rds", region_name=os.environ["APP_AWS_REGION"])
    token = client.generate_db_auth_token(
        DBHostname=os.environ["DBT_HOST"],
        Port=int(os.environ.get("DBT_PORT", "5432")),
        DBUsername=os.environ.get("DBT_USER", "events"),
    )
    print(token, end="")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Mint the token before running dbt, on AWS only**

`dbt/scripts/run_and_publish.sh` — add at the very top, before the existing
`set +e`:

```sh
#!/bin/sh
if [ "$APP_ENVIRONMENT" = "aws" ]; then
  export DBT_PASSWORD=$(python scripts/generate_db_token.py)
fi

set +e
```

- [ ] **Step 4: Build and push the dbt image — arm64, first time ever for this repo**

```bash
docker build --platform linux/arm64 --target runtime-dbt -t events-api-dbt:local .
docker tag events-api-dbt:local 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-dbt:latest
docker push 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-dbt:latest
```

- [ ] **Step 5: ServiceAccount and CronJob manifests**

`k8s/overlays/aws-dbt/service-account.yaml` (get the real ARN from Task 6,
Step 4 first):

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: events-api-dbt
  namespace: events-api
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::938500344309:role/events-api-iam-dbt-irsa
```

`k8s/overlays/aws-dbt/dbt-cronjob.yaml` — get the real RDS endpoint via
`cd terraform && terraform output -raw rds_endpoint` (same value already
used in `k8s/overlays/aws/app-configmap-patch.yaml`'s `APP_DB_HOST`):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: dbt-build
  namespace: events-api
spec:
  schedule: "0 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          serviceAccountName: events-api-dbt
          restartPolicy: OnFailure
          containers:
            - name: dbt-build
              image: 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-dbt:latest
              imagePullPolicy: Always
              command: ["sh", "scripts/run_and_publish.sh"]
              env:
                - name: APP_ENVIRONMENT
                  value: aws
                - name: APP_AWS_REGION
                  value: eu-central-1
                - name: DBT_HOST
                  value: events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com
                - name: DBT_PORT
                  value: "5432"
                - name: DBT_USER
                  value: events
                - name: DBT_DBNAME
                  value: events
                - name: DBT_SSLMODE
                  value: require
              resources:
                requests:
                  cpu: 100m
                  memory: 256Mi
                limits:
                  cpu: 500m
                  memory: 512Mi
```

`k8s/overlays/aws-dbt/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - service-account.yaml
  - dbt-cronjob.yaml
```

- [ ] **Step 6: Apply and manually trigger one real run**

```bash
kubectl apply -k k8s/overlays/aws-dbt
kubectl create job --from=cronjob/dbt-build dbt-build-manual-1 -n events-api
kubectl -n events-api logs -f job/dbt-build-manual-1
```

Expected: clean `dbt build` output ending in success (or legitimate model
test failures if data is genuinely stale — either way, no connection error,
no Python traceback), then `data_quality_runs updated: passed=..., N checks`
printed by `publish_data_quality.py`.

- [ ] **Step 7: Confirm the row landed, and the Job succeeded**

```bash
kubectl -n events-api get job dbt-build-manual-1
```

Expected: `COMPLETIONS: 1/1`.

```bash
kubectl -n events-api port-forward svc/events-api 8000:8000
curl -s http://localhost:8000/health/data-quality | python3 -m json.tool
```

Expected: a real report with a `generated_at` timestamp from just now.

---

### Task 8: CloudWatch metric push + Alarm + SNS notification

**Files:**
- Modify: `dbt/scripts/publish_data_quality.py`
- Create: `terraform/observability.tf`

**Interfaces:**
- Consumes: Task 7's working CronJob, Task 6's `cloudwatch:PutMetricData`
  grant.
- Produces: a live `EventsApi/DataQuality`/`DbtBuildPassed` CloudWatch metric,
  and an alarm watching it.

- [ ] **Step 1: Push the metric from the same script that already computes `passed`**

`dbt/scripts/publish_data_quality.py` — add the import at the top, and the
push at the end of `main()`, after the existing Postgres upsert:

```python
import os

import boto3
import psycopg2
```

```python
def main() -> None:
    generated_at, passed, checks = build_report()

    conn = psycopg2.connect(
        host=os.environ.get("DBT_HOST", "localhost"),
        port=os.environ.get("DBT_PORT", "5432"),
        user=os.environ.get("DBT_USER", "events"),
        password=os.environ.get("DBT_PASSWORD", "events"),
        dbname=os.environ.get("DBT_DBNAME", "events"),
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_quality_runs (id, generated_at, passed, checks, updated_at)
                VALUES (1, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    generated_at = EXCLUDED.generated_at,
                    passed = EXCLUDED.passed,
                    checks = EXCLUDED.checks,
                    updated_at = now()
                """,
                (generated_at, passed, json.dumps(checks)),
            )
    finally:
        conn.close()

    print(f"data_quality_runs updated: passed={passed}, {len(checks)} checks")

    # Second destination for the same passed boolean — a genuinely different
    # signal type (continuous Prometheus-style metrics don't fit a one-shot
    # batch job's exit status naturally) gets a genuinely different tool.
    if os.environ.get("APP_ENVIRONMENT") == "aws":
        cloudwatch = boto3.client("cloudwatch", region_name=os.environ["APP_AWS_REGION"])
        cloudwatch.put_metric_data(
            Namespace="EventsApi/DataQuality",
            MetricData=[
                {
                    "MetricName": "DbtBuildPassed",
                    "Value": 1.0 if passed else 0.0,
                    "Unit": "None",
                }
            ],
        )
        print(f"CloudWatch metric pushed: DbtBuildPassed={1.0 if passed else 0.0}")
```

- [ ] **Step 2: SNS topic, subscription, and the alarm**

`terraform/observability.tf`:

```hcl
resource "aws_sns_topic" "dbt_build_alerts" {
  name = "events-api-dbt-build-alerts"
}

resource "aws_sns_topic_subscription" "dbt_build_alerts_email" {
  topic_arn = aws_sns_topic.dbt_build_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_notification_email
}

resource "aws_cloudwatch_metric_alarm" "dbt_build_failed" {
  alarm_name          = "events-api-dbt-build-failed"
  alarm_description   = "dbt CronJob build failed, or didn't run at all this hour"
  namespace           = "EventsApi/DataQuality"
  metric_name         = "DbtBuildPassed"
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  # A run that crashes before ever reaching the metric push (e.g. dbt can't
  # even connect) produces no data point at all for that period, not a 0 —
  # "breaching" on missing data is what catches that case too, not just an
  # explicit reported failure. Same fail-closed bias this repo's RLS policy
  # and freshness-report logic already use elsewhere.
  treat_missing_data = "breaching"
  alarm_actions       = [aws_sns_topic.dbt_build_alerts.arn]
  ok_actions          = [aws_sns_topic.dbt_build_alerts.arn]
}
```

- [ ] **Step 3: Apply**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform plan
```

Expect 3 additions (`aws_sns_topic`, `aws_sns_topic_subscription`,
`aws_cloudwatch_metric_alarm`), 0 changes, 0 destroys.

```bash
AWS_PROFILE=events-api-tf terraform apply
```

- [ ] **Step 4: Confirm the email subscription — a real, hands-on step**

Check the inbox at `var.budget_notification_email`'s address for an SNS
subscription-confirmation email and click "Confirm subscription." Until this
is done, `aws sns get-subscription-attributes` reports
`PendingConfirmation` and no notification will ever actually deliver.

- [ ] **Step 5: Rebuild/push the dbt image with Step 1's code change, rerun, confirm a real metric point**

```bash
docker build --platform linux/arm64 --target runtime-dbt -t events-api-dbt:local .
docker tag events-api-dbt:local 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-dbt:latest
docker push 938500344309.dkr.ecr.eu-central-1.amazonaws.com/events-api-dbt:latest
kubectl create job --from=cronjob/dbt-build dbt-build-manual-2 -n events-api
kubectl -n events-api logs -f job/dbt-build-manual-2
```

Expected: the new `CloudWatch metric pushed: DbtBuildPassed=...` line in the
output.

```bash
AWS_PROFILE=events-api-tf aws cloudwatch get-metric-statistics \
  --namespace EventsApi/DataQuality --metric-name DbtBuildPassed \
  --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%S)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%S)" \
  --period 3600 --statistics Minimum
```

Expected: one datapoint, `Minimum` of `0.0` or `1.0` matching what the job
actually reported.

---

### Task 9: Deliberate failure — prove the alarm and SNS notification actually work

**Files:** none — this is a live operational test, not a code change.

**Interfaces:**
- Consumes: Task 8's alarm/topic, Task 7's working CronJob.

- [ ] **Step 1: Force a connection-level failure, not just a model-level one**

Point `DBT_DBNAME` at a database that doesn't exist. This is deliberately a
harder failure than a failing dbt test — it makes `dbt build` fail before
`publish_data_quality.py` ever runs, so **no CloudWatch metric point gets
published at all** for this run. This is exactly the case
`treat_missing_data = "breaching"` was chosen for — proving the alarm fires
on absence, not only on an explicit reported failure.

```bash
kubectl -n events-api set env cronjob/dbt-build DBT_DBNAME=nonexistent_db
kubectl create job --from=cronjob/dbt-build dbt-build-manual-fail -n events-api
kubectl -n events-api logs -f job/dbt-build-manual-fail
```

Expected: a dbt connection error, no `data_quality_runs updated` line, no
`CloudWatch metric pushed` line — this run publishes nothing anywhere.

- [ ] **Step 2: Wait for the alarm to evaluate, confirm `ALARM` and the SNS email**

CloudWatch alarms on a 3600s period take up to that long to naturally
transition on missing data. To verify without waiting a full hour, force an
alarm re-evaluation is not directly possible via the CLI — instead, confirm
the mechanism is wired correctly by checking state after the next natural
period boundary, or (faster) temporarily note the alarm's current state,
then poll:

```bash
watch -n 60 "AWS_PROFILE=events-api-tf aws cloudwatch describe-alarms --alarm-names events-api-dbt-build-failed --query 'MetricAlarms[0].{state:StateValue,reason:StateReason}'"
```

Expected within the hour: `state: ALARM`. Confirm the actual SNS email
arrives at the same time — this is the real proof, not just the API-reported
state.

- [ ] **Step 3: Revert, confirm recovery**

```bash
kubectl -n events-api set env cronjob/dbt-build DBT_DBNAME=events
kubectl create job --from=cronjob/dbt-build dbt-build-manual-recover -n events-api
kubectl -n events-api logs -f job/dbt-build-manual-recover
```

Expected: a real `CloudWatch metric pushed` line again. Poll
`describe-alarms` again — expected: `state: OK` within the next period, and
a second SNS email (the `ok_actions` notification) confirming recovery
alerts work too, not just failure alerts.

---

### Task 10: Document and commit

**Files:**
- Modify: `WHATS_NEXT.md`

- [ ] **Step 1: Write up what was actually built and verified**, once Tasks
  1-9 are all genuinely done — the capacity trim decision and its reasoning,
  the dbt-on-AWS port (a real prerequisite this milestone absorbed, not
  originally scoped), the CloudWatch/SNS mechanism, and the concrete
  live-verification results (pod scheduling outcome, dashboard screenshot
  reference, alarm state transitions actually observed) — same style as
  every other entry already in `WHATS_NEXT.md`'s "Current state" section.

- [ ] **Step 2: Remind about teardown/cost** — this milestone adds
  `kube-prometheus-stack`'s pods (small, no new nodes if the trim held), one
  SNS topic/subscription (free), one CloudWatch alarm (near-free), and the
  `dbt_irsa` role. Per `AWS_PLAN.md`'s per-milestone discipline, remind
  whether this session is ending (full `-target=` teardown of
  `module.eks`/`module.rds`/`module.networking` plus this milestone's new
  Terraform resources) or continuing into Milestone 7 (leave it running).

- [ ] **Step 3: Hand over the commit** — don't run it. Something like:

```bash
git add app/main.py pyproject.toml uv.lock k8s/base/service.yaml \
  k8s/overlays/aws-observability k8s/overlays/aws-dbt \
  helm/kube-prometheus-stack terraform/modules/iam terraform/outputs.tf \
  terraform/observability.tf dbt/profiles.yml dbt/scripts \
  WHATS_NEXT.md
git commit -m "$(cat <<'EOF'
Milestone 6: kube-prometheus-stack + app instrumentation, dbt CronJob on AWS, CloudWatch/SNS alerting

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VRfHrAhWBSABwExX5tD1bW
EOF
)"
```
