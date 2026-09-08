# AWS Milestone 7 (HPA, made genuinely verifiable) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work.

**Goal:** A real HPA scale-up, actually exercised — `kubectl get hpa` shows
`events-api`'s replica count climb under real, load-generated CPU pressure,
confirmed visually against the existing Grafana dashboard, not
configured-and-never-tested.

**Architecture:** `metrics-server` installed via `helm install` (this
project's established raw-Helm precedent for cluster addons — no
Kubernetes/Helm Terraform provider anywhere in this repo), because HPA's
CPU-based scaling has no metrics source without it. An `autoscaling/v2`
`HorizontalPodAutoscaler` targets the already-resourced `events-api`
Deployment. A `curlimages/curl` Job, run in-cluster against the Service's
ClusterIP, drives real concurrent load against `/healthz` — chosen
specifically because it's CPU-bound and DB-free, unlike `POST /events`,
which would stress `db.t4g.micro` instead of pod CPU and risk re-triggering
Milestone 5's RDS-storage incident via the currently-idle, unconsumed
Debezium replication slots. Two new Grafana panels (HPA current/desired
replicas) join the existing replica-count panel already kept for this
purpose.

**Tech Stack:** `metrics-server` 3.14.0 (Helm), Kubernetes `autoscaling/v2`,
`curlimages/curl:8.11.0` (confirmed via `docker buildx imagetools inspect`
to publish a real `linux/arm64` manifest — the whole cluster is
`t4g.small`/Graviton).

**Spec:** `docs/superpowers/specs/2026-09-08-aws-milestone-7-hpa-design.md`
— this plan implements that design directly; read both.

## Global Constraints

- `AWS_PROFILE=events-api-tf` for every AWS CLI call. Region `eu-central-1`,
  account `938500344309`, cluster `events-api-eks`.
- EKS and RDS are both confirmed live already (`ACTIVE`/`available`) —
  no `terraform apply` needed anywhere in this plan. No Terraform changes
  at all, in fact: this milestone is entirely cluster-side.
- The CDC/Mongo/realtime stack (Strimzi, Kafka Connect, MongoDB, the
  consumer, the realtime relay) stays scaled to zero throughout — don't
  touch it, don't scale it back up "to check."
- `events-api`'s existing resource requests/limits (`100m`/`500m` CPU,
  `128Mi`/`256Mi` memory) are not touched anywhere in this plan — no
  app-level gap exists to fix first.
- Node fleet is confirmed non-blocking for this milestone: `ip-10-0-11-18`
  and `ip-10-0-11-186` are at 9% CPU / 7% memory requested with only
  DaemonSets, and both have 8 of 11 allocatable pod slots free. Don't
  re-litigate capacity — if a step's live output contradicts this, stop
  and report the actual numbers rather than pushing forward.
- Don't run `git commit` unless explicitly asked in that turn — the final
  task hands over the command, doesn't run it.

---

### Task 1: Install `metrics-server`

**Files:**
- Create: `helm/metrics-server/values-override.yaml`

**Interfaces:**
- Produces: a working Metrics API (`v1beta1.metrics.k8s.io`) that Task 2's
  HPA depends on entirely — nothing in this task depends on the app or the
  Deployment.

- [ ] **Step 1: Add the Helm repo**

```bash
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo update metrics-server
```

- [ ] **Step 2: Write the values override**

Create `helm/metrics-server/values-override.yaml`:

```yaml
# EKS kubelet serving certs aren't signed by the cluster CA by default —
# metrics-server's own docs (README.md) state the requirement plainly:
# "the Kubelet certificate should be signed by the cluster Certificate
# Authority, or certificate validation must be disabled." This is the
# AWS-documented mitigation for EKS specifically, not a workaround.
args:
  - --kubelet-insecure-tls

resources:
  requests:
    cpu: 100m
    memory: 200Mi
  limits:
    cpu: 200m
    memory: 400Mi
```

- [ ] **Step 3: Install into `kube-system`**

```bash
export AWS_PROFILE=events-api-tf
aws eks update-kubeconfig --name events-api-eks --region eu-central-1
helm install metrics-server metrics-server/metrics-server \
  --namespace kube-system \
  --version 3.14.0 \
  -f helm/metrics-server/values-override.yaml
```

- [ ] **Step 4: Verify the APIService is actually serving**

```bash
kubectl get apiservice v1beta1.metrics.k8s.io
```

Expected: `AVAILABLE` column shows `True`. If it instead shows a message
mentioning a certificate error, the `--kubelet-insecure-tls` arg didn't
take — check `kubectl get deploy -n kube-system metrics-server -o
jsonpath='{.spec.template.spec.containers[0].args}'` and confirm the flag
is actually present. If it times out rather than erroring on certs, that's
node security-group ingress on kubelet port 10250, not this flag — a
different problem, stop and report rather than guessing further.

- [ ] **Step 5: Verify real numbers, not just object existence**

```bash
kubectl top pods -n events-api
```

Expected: real CPU/memory numbers for the `events-api` pod. It's normal
(not a bug) for this to return no data or an error for the first
15-60 seconds after install — metrics-server's first scrape cycle needs to
complete first. Retry after a short wait before treating this as broken.

- [ ] **Step 6: Commit**

```bash
git add helm/metrics-server/values-override.yaml
git commit -m "$(cat <<'EOF'
Install metrics-server on EKS via Helm

HPA's CPU-based scaling has no metrics source without it. EKS kubelet
serving certs aren't cluster-CA-signed, so --kubelet-insecure-tls is
required (confirmed against metrics-server's own docs, not assumed).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VRfHrAhWBSABwExX5tD1bW
EOF
)"
```

---

### Task 2: Stop the base Deployment from fighting the HPA over `replicas`, add the HPA

**Files:**
- Modify: `k8s/base/deployment.yaml`
- Create: `k8s/overlays/aws/hpa.yaml`
- Modify: `k8s/overlays/aws/kustomization.yaml`

**Interfaces:**
- Consumes: Task 1's working Metrics API.
- Produces: `events-api` Deployment in `events-api` namespace, scaled by a
  live `HorizontalPodAutoscaler` named `events-api`. Task 4's load-gen Job
  targets this Deployment indirectly (via the Service, unchanged).

- [ ] **Step 1: Remove the hardcoded replica count from the base manifest**

In `k8s/base/deployment.yaml`, delete the `replicas: 1` line entirely:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: events-api
  namespace: events-api
spec:
  selector:
    matchLabels:
      app: events-api
  template:
```

(Everything below `template:` stays exactly as it already is — only the
`replicas: 1` line above `selector:` is removed.)

This is why: left in place, every future `kubectl apply -k
k8s/overlays/aws` (e.g. a normal image-update redeploy) explicitly asserts
`spec.replicas: 1` again, and the HPA then scales it back up on its next
15-second reconcile — a real, visible flap. Harmless for `kind`, which has
no HPA at all: Kubernetes defaults an omitted `replicas` to `1` on initial
creation.

- [ ] **Step 2: Write the HPA manifest**

Create `k8s/overlays/aws/hpa.yaml`:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: events-api
  namespace: events-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: events-api
  minReplicas: 1
  maxReplicas: 4
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50
```

`maxReplicas: 4` is sized to the confirmed pod-count-per-node headroom
(see the spec's capacity section), not a round-number guess. `50`% is
against the pod's **request** (`100m`), not its `500m` limit — so a fully
CPU-saturated pod can legitimately report up to ~500% utilization; that's
expected, not a bug, and it means this target triggers scale-up once a
pod sustains roughly 50m of real CPU.

- [ ] **Step 3: Wire the HPA into the aws overlay**

In `k8s/overlays/aws/kustomization.yaml`, add `hpa.yaml` to `resources`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../../base
  - service-account.yaml
  - migration-service-account.yaml
  - hpa.yaml

patches:
  - path: delete-postgres-deployment.yaml
  - path: delete-postgres-service.yaml
  - path: delete-postgres-secret.yaml
  - path: delete-app-secret.yaml
  - path: app-configmap-patch.yaml
  - path: deployment-patch.yaml
  - path: migration-job-patch.yaml
```

- [ ] **Step 4: Dry-run render before touching the cluster**

```bash
kubectl kustomize k8s/overlays/aws | grep -B2 -A20 "kind: HorizontalPodAutoscaler"
```

Expected: the HPA renders with `maxReplicas: 4`, `averageUtilization: 50`,
targeting `Deployment/events-api`.

```bash
kubectl kustomize k8s/overlays/aws | grep -B5 -A3 "kind: Deployment$"
```

Expected: no `replicas:` line anywhere under the `events-api` Deployment's
`spec:`.

- [ ] **Step 5: Apply for real**

```bash
kubectl apply -k k8s/overlays/aws
```

- [ ] **Step 6: Verify the HPA is live**

```bash
kubectl get hpa -n events-api
kubectl describe hpa -n events-api events-api
```

Expected immediately after creation: `TARGETS` column may show
`<unknown>/50%` for the first 15-60 seconds while metrics-server populates
its first data point for this specific pod — expected, not a
misconfiguration. Within a minute it should resolve to a real percentage
(likely near-idle, e.g. low single digits, since no load has been
generated yet).

- [ ] **Step 7: Confirm the replicas field doesn't fight the HPA on a normal reapply**

```bash
kubectl apply -k k8s/overlays/aws
kubectl get deploy -n events-api events-api -o jsonpath='{.spec.replicas}{"\n"}'
kubectl get hpa -n events-api events-api
```

Expected: whatever `spec.replicas` reports here, the HPA's own `REPLICAS`
column stays consistent (or self-corrects within one 15-second reconcile)
— report the actual observed values rather than assuming this is clean,
since this is exactly the failure mode Step 1 exists to prevent.

- [ ] **Step 8: Commit**

```bash
git add k8s/base/deployment.yaml k8s/overlays/aws/hpa.yaml k8s/overlays/aws/kustomization.yaml
git commit -m "$(cat <<'EOF'
Add HPA for events-api, stop the base Deployment from owning replicas

autoscaling/v2 HPA, CPU utilization target 50% of the existing 100m
request, maxReplicas 4 (sized to confirmed per-node pod-count headroom).
Dropped the base Deployment's hardcoded replicas: 1 so a normal kubectl
apply -k k8s/overlays/aws stops fighting the HPA for that field.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VRfHrAhWBSABwExX5tD1bW
EOF
)"
```

---

### Task 3: Add HPA replica panels to the existing Grafana dashboard

**Files:**
- Modify: `k8s/overlays/aws-observability/grafana-dashboard-configmap.yaml`

**Interfaces:**
- Consumes: kube-state-metrics (already running, confirmed live in
  Milestone 6) exposing `kube_horizontalpodautoscaler_status_*` once
  Task 2's HPA object exists.
- Produces: a 4th panel on the `events-api` dashboard, alongside the
  existing request-rate/latency/replica-count panels Task 5's
  verification reuses.

- [ ] **Step 1: Add the panel**

In `k8s/overlays/aws-observability/grafana-dashboard-configmap.yaml`, add a
4th entry to the `panels` array (after the existing `id: 3` panel, note the
added trailing comma on the `id: 3` panel's closing brace):

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
        },
        {
          "id": 4,
          "title": "HPA current vs desired replicas",
          "type": "timeseries",
          "gridPos": { "h": 8, "w": 24, "x": 0, "y": 16 },
          "targets": [
            {
              "expr": "kube_horizontalpodautoscaler_status_current_replicas{horizontalpodautoscaler=\"events-api\", namespace=\"events-api\"}",
              "legendFormat": "current"
            },
            {
              "expr": "kube_horizontalpodautoscaler_status_desired_replicas{horizontalpodautoscaler=\"events-api\", namespace=\"events-api\"}",
              "legendFormat": "desired"
            }
          ]
        }
      ]
    }
```

- [ ] **Step 2: Apply**

```bash
kubectl apply -k k8s/overlays/aws-observability
```

(This ConfigMap isn't Helm-templated — the dashboard sidecar picks up any
ConfigMap labeled `grafana_dashboard: "1"` automatically, no Helm
values change or restart needed, per Milestone 6's own provisioning setup.)

- [ ] **Step 3: Verify the panel exists in Grafana**

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

In a browser, open `http://localhost:3000`, log in (Milestone 6's
auto-generated admin password — `kubectl get secret -n monitoring
kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64
-d`), open the `events-api` dashboard, and confirm the 4th panel ("HPA
current vs desired replicas") is present. It will show no data yet —
expected, since the HPA has nothing to report until Task 4 generates load.

- [ ] **Step 4: Commit**

```bash
git add k8s/overlays/aws-observability/grafana-dashboard-configmap.yaml
git commit -m "$(cat <<'EOF'
Add HPA current/desired replica panels to the events-api Grafana dashboard

kube-state-metrics already exposes these; makes the scale-up/cool-down
gap visible alongside the existing replica-count panel.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VRfHrAhWBSABwExX5tD1bW
EOF
)"
```

---

### Task 4: Load-generation Job, and the real end-to-end scale-up/scale-down proof

**Files:**
- Create: `k8s/overlays/aws/load-test-job.yaml`

**Interfaces:**
- Consumes: Task 2's HPA and Task 1's metrics-server; hits the existing
  `events-api` Service (`k8s/base/service.yaml`, unmodified) at
  `events-api.events-api.svc.cluster.local:8000/healthz`.
- Produces: nothing later tasks depend on — this is the milestone's actual
  verification, not infrastructure.

Deliberately **not** added to `k8s/overlays/aws/kustomization.yaml`'s
`resources` — a completed Job is immutable, so having it permanently
tracked would make every future `kubectl apply -k k8s/overlays/aws` fail
once it's run once. Applied standalone, on purpose.

- [ ] **Step 1: Write the load-generation Job**

Create `k8s/overlays/aws/load-test-job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: events-api-load-test
  namespace: events-api
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: load-test
          image: curlimages/curl:8.11.0
          resources:
            requests:
              cpu: 50m
              memory: 32Mi
            limits:
              cpu: 100m
              memory: 64Mi
          command: ["/bin/sh", "-c"]
          args:
            - |
              END=$(( $(date +%s) + 240 ))
              i=0
              while [ $i -lt 20 ]; do
                (
                  while [ "$(date +%s)" -lt "$END" ]; do
                    curl -s -o /dev/null http://events-api.events-api.svc.cluster.local:8000/healthz
                  done
                ) &
                i=$((i+1))
              done
              wait
```

20 parallel tight-loop `curl`s against `/healthz` for 240 seconds (4
minutes) — POSIX `date +%s` polling rather than a bash-only `$SECONDS`,
since this image's shell is BusyBox `ash`/`sh`, not bash.

- [ ] **Step 2: Confirm baseline before the load starts**

```bash
kubectl get hpa -n events-api events-api
kubectl get pods -n events-api -l app=events-api
```

Record the actual replica count and `TARGETS` percentage here — this is
the "before" state the load run is compared against.

- [ ] **Step 3: Run the load Job**

```bash
kubectl apply -f k8s/overlays/aws/load-test-job.yaml
```

- [ ] **Step 4: Watch the HPA react, live, for the Job's ~4-minute run**

```bash
kubectl get hpa -n events-api -w
```

Expected: `TARGETS` climbs from its baseline, and once it crosses `50%`,
`REPLICAS` increases (up to `maxReplicas: 4`). Record the actual peak
`TARGETS`/`REPLICAS` values observed — don't predict them in advance.

In a second terminal, confirm new pods actually scheduled on the expected
nodes (the two near-idle ones, not the already-pod-count-capped one):

```bash
kubectl get pods -n events-api -l app=events-api -o wide
```

- [ ] **Step 5: Confirm the Job finished and check for throttling on the load-test pod itself**

```bash
kubectl get job -n events-api events-api-load-test
kubectl logs -n events-api job/events-api-load-test --tail=5
```

Expected: `COMPLETIONS 1/1`. If the load-test pod's own `100m` CPU limit
throttled it hard enough to suppress load generation, `kubectl top pod`
during Step 4 would have shown it pinned near `100m` the whole time — if
`REPLICAS` never moved at all, check this before assuming the HPA itself
is broken.

- [ ] **Step 6: Watch the scale-down cool-off**

```bash
kubectl get hpa -n events-api -w
```

Expected: `TARGETS` drops back toward baseline once the Job's traffic
stops, but `REPLICAS` stays elevated for the HPA's default 300-second
`scaleDown.stabilizationWindowSeconds` before decreasing — this is a real
HPA behavior to observe directly, not a bug to work around. Let it run the
full ~5 minutes rather than interrupting.

- [ ] **Step 7: If `REPLICAS` never moved, don't guess — check these in order before re-running**

1. `kubectl top pod -n events-api -l app=events-api` during a fresh
   run — is the existing pod's CPU usage actually climbing at all?
2. If CPU stays flat, the load isn't reaching the pod — check
   `kubectl logs -n events-api job/events-api-load-test` for curl errors
   (DNS resolution failures against the Service name are the likely
   culprit).
3. If CPU climbs but never crosses 50%, `20` parallel loops wasn't enough
   concurrency — delete the Job (`kubectl delete job -n events-api
   events-api-load-test`) and re-apply after raising the loop count (e.g.
   `40`) in Step 1's `while [ $i -lt 20 ]` line.

- [ ] **Step 8: Rerun note for later**

A completed Job can't be reapplied with the same name — a second run
needs:

```bash
kubectl delete job -n events-api events-api-load-test
kubectl apply -f k8s/overlays/aws/load-test-job.yaml
```

- [ ] **Step 9: Commit**

```bash
git add k8s/overlays/aws/load-test-job.yaml
git commit -m "$(cat <<'EOF'
Add in-cluster load-generation Job for HPA verification

Targets /healthz specifically (CPU-bound, no DB writes) rather than
POST /events, which would stress db.t4g.micro instead of pod CPU and
risk re-triggering the Milestone 5 RDS-storage incident via the
currently-idle, unconsumed Debezium replication slots.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VRfHrAhWBSABwExX5tD1bW
EOF
)"
```

---

### Task 5: Visual confirmation against Grafana, then record real findings

**Files:**
- Modify: `WHATS_NEXT.md`

**Interfaces:**
- Consumes: Task 4's load run (rerun it if it already fully cooled down
  before this task starts — the Grafana panels need to actually show
  motion during observation, not just in `kubectl`'s scrollback).

- [ ] **Step 1: Rerun the load Job for a fresh, observed window**

```bash
kubectl delete job -n events-api events-api-load-test --ignore-not-found
kubectl apply -f k8s/overlays/aws/load-test-job.yaml
```

- [ ] **Step 2: Port-forward Grafana and open the dashboard**

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

- [ ] **Step 3: Visually confirm the panels move, using the browser**

Open `http://localhost:3000`, navigate to the `events-api` dashboard, and
watch it for the ~4-minute duration of the load Job plus a bit of the
cool-down after. Confirm, by actually looking at it (not by inference):
- "Request rate" spikes during the load window.
- "events-api replica count" climbs from its baseline toward `4` (or
  whatever the real observed ceiling was in Task 4).
- "HPA current vs desired replicas" shows `desired` leading `current`
  briefly during scale-up, then both settling together, then both staying
  elevated through the 300-second stabilization window before dropping.

Take a screenshot once the replica-count panel visibly shows an increase
— this is the milestone's actual verification bar
(`AWS_PLAN.md`: "`kubectl get hpa` shows a real replica-count change
during the [load test]"), same "real traffic, visually confirmed against
the dashboard" bar Milestone 6 already established.

- [ ] **Step 4: Update `WHATS_NEXT.md` with the real, observed numbers**

Add a new entry under the Milestone 6 entry in `WHATS_NEXT.md`, following
that file's existing style (what was built, what broke, what was verified
live) — write the actual `TARGETS`/`REPLICAS` peak values and the actual
elapsed time for scale-up and scale-down observed in Task 4/this task, not
predicted ones. Do not copy this plan's example numbers (`50%`, `4`,
`240` seconds) into `WHATS_NEXT.md` as if they were the observed result —
they're the *configured* target and duration; the *observed* peak
utilization, actual replica count reached, and actual scale-down timing
are separate facts to record from what you actually saw in Steps 3/4 of
Task 4 and Step 3 here.

- [ ] **Step 5: Clean up the completed load-test Job**

```bash
kubectl delete job -n events-api events-api-load-test
```

(The Job's manifest file stays committed — only the completed Job object
itself is transient.)

- [ ] **Step 6: Hand over the final commit**

```bash
git add WHATS_NEXT.md
git commit -m "$(cat <<'EOF'
Record Milestone 7 (HPA) verification results in WHATS_NEXT.md

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VRfHrAhWBSABwExX5tD1bW
EOF
)"
```

Per this repo's own discipline: don't run this commit unless explicitly
asked to in this turn.

- [ ] **Step 7: Teardown reminder, not part of this task's deliverable**

This milestone added no new AWS/Terraform resources, so there's nothing
new to `terraform destroy`. The existing per-milestone teardown discipline
for EKS/RDS/networking (already live from prior sessions) still applies
before ending the session, per `AWS_PLAN.md`'s standing convention —
confirm with the user whether to tear down now or leave it live for the
next milestone (RBAC, Milestone 8).
