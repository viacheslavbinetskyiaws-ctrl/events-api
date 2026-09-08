# AWS Milestone 7 (HPA, made genuinely verifiable) — Design

## Context

`AWS_PLAN.md`'s Milestone 7 scope: "A small load-generation script actually
drives the app's CPU up, so a real scale-up is observable via `kubectl get
hpa` and Grafana (Milestone 6) — not configured-and-never-exercised."

EKS and RDS are both still live from the Milestone 6 session (confirmed via
`terraform state list` plus live `describe-cluster`/`describe-db-instances`
calls — `ACTIVE`/`available`). The CDC/Mongo/realtime stack (Strimzi, Kafka
Connect, MongoDB, the consumer, the realtime relay) is still scaled to zero,
as Milestone 6 deliberately left it, and stays that way — nothing in this
milestone needs it, and it's exactly the headroom this design relies on.

`metrics-server` was confirmed absent before any design work started
(`kubectl get deploy -n kube-system metrics-server` → `NotFound`,
`kubectl get apiservice v1beta1.metrics.k8s.io` → `NotFound`) — it's real,
necessary prerequisite work for this milestone, not something already sitting
there. HPA's CPU-based scaling cannot function without it.

## Real state checked before designing (not assumed)

- `events-api`'s Deployment already has CPU requests/limits set
  (`100m`/`500m`) — no app-level gap to fix before HPA can compute
  utilization.
- Node fleet: 4× `t4g.small`, 3 in `events-api-default` + 1 tainted
  `dedicated=kafka-connect:NoSchedule` in `events-api-kafka-connect`. The
  account's 8-vCPU quota (confirmed hard-capped in Milestone 6) only bites
  when **adding nodes** — HPA only adds pods to existing nodes, so this
  quota is not a constraint for this milestone. No Cluster Autoscaler or
  Karpenter is installed (confirmed via `terraform state list` and the pod
  inventory), so node count never changes as a side effect of this work.
- Real per-node headroom, checked live: `ip-10-0-11-18` and
  `ip-10-0-11-186` sit at 9% CPU / 7% memory requested, carrying only
  DaemonSets. `ip-10-0-10-232` (current home of the single `events-api`
  pod, plus Grafana/kube-state-metrics/the Prometheus operator) is at 37%
  CPU / 89% memory requested. The tainted Kafka Connect node carries
  Prometheus's own pod (tolerated) at 14%/26%.
- **Pod-count-per-node**, the constraint the milestone prompt didn't
  name: each node's `status.allocatable.pods` is 11. `ip-10-0-10-232` is
  already at its cap (11/11 pods scheduled) — a pre-existing condition,
  not something this milestone causes. `ip-10-0-11-18` and
  `ip-10-0-11-186` carry only 3 pods each, so 8 more fit on each before
  hitting the same cap. `maxReplicas: 4` for `events-api` needs at most 3
  *additional* scheduling slots (1 replica already exists), which fits
  comfortably on the two idle nodes even if `ip-10-0-10-232` accepts none.
- `events-api`'s Service (`k8s/base/service.yaml`) exposes a named `http`
  port, `port: 8000` → `targetPort: 8000`, matching the container port —
  reachable in-cluster at `events-api.events-api.svc.cluster.local:8000`.
- No `HorizontalPodAutoscaler`/`autoscaling/v2`/`metrics-server` reference
  exists anywhere in this repo today (grepped across `*.yaml`/`*.md`/`*.tf`)
  — this is genuinely new surface, not a dormant resource being re-enabled.

## metrics-server's TLS requirement, verified against its own docs (context7, not memory)

Requirement, quoted from the project's actual `README.md`: "the Kubelet
certificate should be signed by the cluster Certificate Authority, or
certificate validation must be disabled." EKS kubelets fall into the second
case — this is why `--kubelet-insecure-tls` is the standard, AWS-documented
mitigation on EKS, not a guess. The chart's `args:` list is where this flag
is set (confirmed against the chart's own known-issues/README content); the
`APIService`'s `insecureSkipTLSVerify: true` is the chart's own default
posture once that flag disables kubelet-side verification.

## Design

### 1. metrics-server

Installed via `helm install` — matches this project's established
raw-Helm precedent for cluster addons (`kube-prometheus-stack`, Strimzi,
the MongoDB Community Operator), not a Kubernetes/Helm Terraform provider
(none exists in this repo, deliberately). Chart:
`metrics-server/metrics-server` from
`https://kubernetes-sigs.github.io/metrics-server/`. Values committed at
`helm/metrics-server/values-override.yaml`, mirroring the
`helm/kube-prometheus-stack/values-override.yaml` precedent — an explicit
`args: [--kubelet-insecure-tls]`, plus modest explicit
`resources.requests`/`limits` (this repo's own Milestone 3 lesson: an unset
request is what got Kafka Connect evicted under memory pressure once
already — don't repeat it, even though headroom is not tight this time).
Namespace: `kube-system` (the chart's own default, matches upstream
convention).

**Verification**: `kubectl get apiservice v1beta1.metrics.k8s.io` reports
`Available=True`; `kubectl top pods -n events-api` returns real numbers
(not an error). Expected transient state, not a bug: for roughly the first
15-60 seconds after install, `kubectl top` may report no data while
metrics-server's first scrape cycle completes.

### 2. The HorizontalPodAutoscaler

New file under `k8s/overlays/aws/` (`hpa.yaml`), `autoscaling/v2`, targeting
the `events-api` Deployment. `minReplicas: 1`, `maxReplicas: 4` — sized to
the pod-count-per-node headroom confirmed above, not a round-number guess.
Resource metric `cpu`, `type: Utilization`, target `50`.

**Documented in advance rather than discovered as a surprise**: utilization
is measured as a percentage of the pod's **request** (`100m`), not its
limit (`500m`) — so a fully CPU-saturated pod (using up to its `500m`
limit) can legitimately report up to ~500% utilization. A 50% target
therefore triggers scale-up once a pod is sustaining roughly 50m of actual
CPU. The real number `kubectl describe hpa` reports during the load test is
what gets recorded in `WHATS_NEXT.md` — not a predicted one.

`behavior.scaleDown.stabilizationWindowSeconds` is left at its Kubernetes
default (300s) rather than shortened. The 5-minute post-load cool-down
before scaling back down is a real, instructive HPA behavior worth
observing directly during verification, not friction to engineer away.

Also expected, not a bug: for roughly the first 15-60 seconds after the HPA
resource is created, `kubectl describe hpa` reports `<unknown>` for the CPU
metric while metrics-server populates its first data points.

### 3. `k8s/base/deployment.yaml`: remove the hardcoded `replicas: 1`

Left as-is, every future `kubectl apply -k k8s/overlays/aws` (e.g. a normal
image-update redeploy) would explicitly reset `spec.replicas` to `1`, and
the HPA would then re-scale back up on its next 15-second reconcile —
functionally harmless but a confusing, visible flap if it happens to occur
during a verification demo. Removing the `replicas` field from the base
manifest lets the HPA (or, on a from-scratch `kind` cluster with no HPA at
all) own that field — Kubernetes defaults an omitted `replicas` to `1` on
initial creation, so this is harmless for the `kind` path, which has no HPA
and doesn't need this milestone's overlay.

### 4. Load-generation Job

In-cluster, not a laptop-side `kubectl port-forward` loop — a port-forward
funnels every request through a single API-server-proxied TCP stream and
would not produce meaningful concurrency. A `curlimages/curl:8.11.0`-based
Kubernetes `Job` — that exact image/tag is already used in this repo's
kind-based `cdc-register-connectors` Job, and confirmed via
`docker buildx imagetools inspect` to publish a real `linux/arm64` manifest
(not yet run on this AWS cluster specifically, but architecture-safe,
checked rather than assumed) — running several
backgrounded parallel `curl` loops against
`http://events-api.events-api.svc.cluster.local:8000/healthz` for
3-5 minutes.

`/healthz`, deliberately, not `POST /events`:
- `/healthz` is a synchronous `def` (`app/api/routers/health.py`), so
  FastAPI dispatches it through the anyio threadpool — real per-request CPU
  work, observed by Milestone 6's Prometheus instrumentator middleware on
  every call, with no downstream dependency.
- `POST /events` would make the workload I/O-bound against
  `db.t4g.micro` rather than CPU-bound on the pod — the opposite of what a
  CPU-target HPA is meant to demonstrate — and would generate sustained
  write volume against RDS while the Debezium replication slots for the
  now-scaled-to-zero CDC stack are sitting idle and unconsumed, which is
  the exact shape of the incident that caused Milestone 5's RDS
  storage-full event. `/healthz` has already been proven (Milestone 6) to
  move the same Grafana request-rate/latency panels this milestone reuses,
  so there is no verification gap being traded away by avoiding it.

The Job's own container gets a small explicit CPU request so it doesn't
compete with the `events-api` pods it's trying to scale.

### 5. Grafana

Reuse the existing `kube_deployment_status_replicas{deployment="events-api",
namespace="events-api"}` panel (`k8s/overlays/aws-observability/grafana-dashboard-configmap.yaml`)
for the "moves during load" verification — it was kept specifically for
this milestone. Add two new panels to the same ConfigMap, cheap since
kube-state-metrics already exposes them:
`kube_horizontalpodautoscaler_status_current_replicas` and
`kube_horizontalpodautoscaler_status_desired_replicas`, both scoped to
`horizontalpodautoscaler="events-api"` — makes the current-vs-desired gap
visible during the scale-up/cool-down window, not just the replica count
itself.

## Explicitly out of scope

- No Terraform changes and no new IAM surface — this milestone is entirely
  cluster-side (`helm install` + plain Kubernetes manifests), matching
  `metrics-server`'s own no-cloud-IAM install path.
- No change to the CDC/Mongo/realtime stack's scaled-to-zero state.
- No shortening of the HPA's scale-down stabilization window (see above).
- No new CPU-bound endpoint added to the app purely to make load
  generation more dramatic — `/healthz` already does real, observable
  per-request work under this instrumentation, and adding one would be a
  fake endpoint serving no purpose beyond this one milestone's demo.

## Verification bar

From `AWS_PLAN.md`'s own Verification section: "Milestone 7: `kubectl get
hpa` shows a real replica-count change during the [load test]." Concretely:
1. `kubectl get hpa -n events-api -w` shows `TARGETS` climb and
   `REPLICAS` increase from `1` toward `4` during the load Job's run.
2. The existing Grafana replica-count panel, plus the two new HPA panels,
   visibly move during the same window — confirmed visually (Playwright),
   matching Milestone 6's own verification bar (a real traffic burst,
   visually confirmed against the dashboard, not just `kubectl`'s text
   output).
3. After the load Job completes, replicas scale back down toward `1` once
   the 300s stabilization window elapses — observed, not rushed.
