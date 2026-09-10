# AWS Milestone 9 (ALB Ingress) — Design

## Context

`AWS_PLAN.md`'s Milestone 9 scope, verbatim: "Real external access via the AWS
Load Balancer Controller (installed via Helm — the AWS-documented standard
path for it, unlike the EBS CSI driver's native-addon path in Milestone 3).
Routes to both backend services: path-based routing, e.g. `/stream/*` → the
Node SSE service (Milestone 4), everything else → the Python app. Replaces
`kubectl port-forward` as the only way in — the one piece of 'production
readiness' that's been faked this entire project."

EKS and RDS are both still live from the Milestone 8 session (confirmed live
this session via `describe-cluster`/`describe-db-instances` — `ACTIVE`/
`available`, not assumed from a prior session's word).

`NEXT_MILESTONE_PROMPT.md` proposed bringing up only a minimal CDC subset
(Kafka broker + Kafka Connect's two Debezium *source* connectors + `realtime`,
explicitly skipping MongoDB, the Python consumer, and the BigQuery sink
connectors) to hold down memory after Milestone 6's real capacity pivot.
**Corrected this session**: the actual reason those pieces were scaled to
zero was a Grafana/Prometheus-driven capacity squeeze during Milestone 6, not
anything specific to Mongo/consumer/BigQuery — and this milestone is a good
opportunity to re-verify the *whole* previously-built Milestone 3/5 pipeline
still works after sitting at zero, not just the slice `/stream/events` needs.
Revised plan: bring the full CDC/streaming stack back up, and treat scaling
down Grafana then Prometheus as a *reactive* fallback if real memory pressure
appears — not a precondition, and not a preemptive skip of Mongo/consumer/
BigQuery.

## Real state checked before designing (not assumed)

- **EKS `ACTIVE`, RDS `available`** — confirmed live via `describe-cluster`/
  `describe-db-instances`.
- **CDC/streaming stack still scaled to zero**: `realtime`,
  `strimzi-cluster-operator`, `mongodb-kubernetes-operator` all `0/0/0`; no
  Kafka/Connect/Mongo pods running, only completed Jobs and the one
  `events-api` pod.
- **All 4 `KafkaConnector` CRs already exist in the cluster**, dormant while
  Kafka Connect itself is down (`bigquery-events-sink`,
  `bigquery-tenant-accounts-sink`, `events-connector`,
  `tenant-accounts-connector`) — bringing Kafka Connect back up reconnects
  all four automatically; nothing needs re-registering.
- **No ALB Controller, no `Ingress`, no `IngressClass` exists anywhere** —
  `kubectl get ingressclass`/`kubectl get ingress -A` both empty,
  `kubectl -n kube-system get deployment aws-load-balancer-controller` not
  found. Existing Helm releases (`helm list -A`): `community-operator`,
  `kube-prometheus-stack`, `metrics-server`, `strimzi-kafka-operator` — no
  Terraform `helm` provider anywhere (`providers.tf` has only `aws`), so
  every one of these went in via bare `helm install`.
- **VPC subnets still correctly tagged** (from Milestone 1): 2 public subnets
  `kubernetes.io/role/elb=1`, 2 private `kubernetes.io/role/internal-elb=1`,
  all four also carrying `kubernetes.io/cluster/events-api-eks=shared`.
- **Node fleet**: 4× `t4g.small` (arm64) — 3 in the main node group
  (`desired=max=3`) plus 1 dedicated, `dedicated=kafka-connect:NoSchedule`
  tainted node (taint still live, confirmed). Real per-node headroom, not
  assumed from Milestone 3's original sizing:

  | Node | Memory requests | Pods | Notes |
  |---|---|---|---|
  | `ip-10-0-10-232` | 1220Mi/89% | **11/11** | Full on *both* memory and pod count — put nothing new here. |
  | `ip-10-0-11-18` | 104Mi/7% | 6/11 | |
  | `ip-10-0-11-186` | 304Mi/22% | 4/11 | |
  | `ip-10-0-11-27` (tainted) | 360Mi/26% | 4/11 | Only Kafka-Connect-tolerating pods land here. |

  **Max pods/node = 11 is a hard ENI-based VPC CNI limit for `t4g.small`**,
  not a config choice — confirmed via `kubectl get nodes -o
  ...status.allocatable.pods`.
- **Real AWS quotas, checked live, not assumed**:
  - EC2 "Running On-Demand Standard" vCPU quota = **8** (`aws service-quotas
    get-service-quota --service-code ec2 --quota-code L-1216C47A`). Current
    fleet: 4×`t4g.small`×2vCPU = 8 — already at the ceiling. No 5th node
    without a quota-increase request. (Previously known; freshly reverified
    this session, not stale.)
  - ALB account limits: 50 ALBs/region, 3000 target groups/region — this
    milestone needs 1 ALB / 2 target groups, nowhere near either limit.
  - `aws freetier get-free-tier-usage`: only "Always Free" entries appear
    (AWS Glue, SQS, CloudWatch — unrelated, unlimited-duration tiers); zero
    EC2/RDS/EKS/ELB entries at all. Confirms this account isn't inside a
    12-month Free Tier window for anything this milestone touches —
    consistent with running on promotional credit (Milestone 0). No Free
    Tier usage cap applies here; the vCPU quota and per-node pod/memory
    limits above are the real ceilings.
- **`realtime/src/index.ts` has no periodic SSE heartbeat** — it sets
  `Connection: keep-alive` but only writes to the response on a real
  broadcast event, never on a timer. A quiet stream would hit ALB's default
  60s idle timeout and look like a flaky ALB rather than a config default.
- **Both backends already serve `/healthz`** (`k8s/base/deployment.yaml` port
  8000, `k8s/overlays/aws-realtime/deployment.yaml` port 3000) — a single
  Ingress-level health-check path annotation covers both target groups
  (health-check port defaults to `traffic-port`, resolved per-target-group).
- **No existing precedent for a `kube-system`-scoped IRSA `ServiceAccount`**
  in this repo — every prior cluster-infra Helm install (Grafana/Prometheus,
  metrics-server, Strimzi) needs no AWS API access, so none carries one. The
  ALB Controller is the first that does.

## Current AWS Load Balancer Controller docs, verified via context7 (not training-data memory)

- Helm install: `helm repo add eks https://aws.github.io/eks-charts`, then
  `helm install aws-load-balancer-controller eks/aws-load-balancer-controller
  -n kube-system --set clusterName=<cluster> --set serviceAccount.create=false
  --set serviceAccount.name=aws-load-balancer-controller` — confirms the
  ServiceAccount must be pre-created (with the IRSA annotation), not left to
  the chart.
- Chart default is **2 replicas** (HA-by-default since chart v1.2.0, soft
  anti-affinity), each requesting 200Mi memory/100m cpu — `replicaCount=1`
  is a documented, supported override.
- `target-type: ip` is forced, not chosen: both backend Services
  (`events-api`, `realtime`) are plain `ClusterIP` with no `NodePort`;
  `instance` mode requires one.
- Rule order is significant: the controller assigns ALB listener-rule
  priority by list position in `spec.rules[].http.paths[]` — a catch-all `/`
  listed before `/stream` would swallow `/stream/events` too.
- `alb.ingress.kubernetes.io/healthcheck-path`,
  `-interval-seconds`, `-timeout-seconds` are real, current Ingress
  annotations (`docs/guide/ingress/annotations.md`).
- `alb.ingress.kubernetes.io/load-balancer-attributes:
  idle_timeout.timeout_seconds=<N>` is a real per-Ingress annotation
  (confirmed via the controller's own IngressGroup-consistency source,
  which lists it as a union-map, per-key-enforced attribute — i.e. it's
  read directly off the Ingress, not only reachable via a separate
  `IngressClassParams` resource).
- The exact current values-key controlling automatic `IngressClass`
  creation wasn't pinned down (renamed across chart versions) — plan is to
  check live after install (`kubectl get ingressclass`) rather than assume
  a default, with the trivial documented fallback (`IngressClass` with
  `controller: ingress.k8s.aws/alb`) ready if needed.
- The `AWSLoadBalancerControllerIAMPolicy` JSON is fetched via the
  project's own documented command:
  `curl -o iam-policy.json
  https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json`
  — vendored into Terraform via `file()`, not hand-translated into
  `aws_iam_policy_document`.

## Decisions made this session

- **No TLS/domain** — ACM can't issue a cert for the ALB's own
  `*.elb.amazonaws.com` name, and Route53 domain registration is a real,
  non-refundable cost with no Free Tier coverage and no promotional-credit
  eligibility (confirmed against AWS's own pricing page). Plain HTTP,
  matching `AWS_PLAN.md`'s scope. Captured as a separate, optional
  follow-on: `AWS_PLAN.md` Milestone 13 (domain + multi-env routing).
- **Full CDC/streaming stack comes back, not a minimal subset** — see
  Context above. Order: `strimzi-cluster-operator` → `Kafka`/`KafkaConnect`
  CRs (all 4 `KafkaConnector`s reconnect automatically) →
  `mongodb-kubernetes-operator` → the `MongoDBCommunity` CR →
  `streaming/consumer.py` → `realtime`.
- **Grafana → Prometheus scaledown is reactive only**, triggered by real
  `kubectl top` pressure, not scheduled preemptively. **Correction from an
  earlier pass of this doc**: Mongo's replica-set member is *not*
  request-free — the Community Operator injects its own default container
  requests regardless of what the CR specifies (`mongod` 500m/400M,
  `mongodb-agent` 500m/400M — confirmed empirically during the original
  Milestone 3 session, `WHATS_NEXT.md`'s own record), ~800Mi total, not 0Mi
  as an earlier draft of this section assumed. Redone: 384 (strimzi-op) + 512
  (Kafka broker) + 200 (mongo-op) + 800 (Mongo member) + 128 (consumer) + 256
  (`realtime`×2) + 200 (ALB controller) = **2480Mi**, against **~2460Mi**
  free on the two eligible nodes — right at the edge, likely slightly over.
  Hitting the Grafana→Prometheus fallback during this milestone is a real
  likely outcome, not a remote contingency. `metrics-server`/
  `kube-state-metrics` stay off-limits regardless (HPA regression risk,
  Milestone 7 precedent).
- **Mongo's `resources` block: pin requests now, defer limits to live data**
  — added to `mongodb-community.yaml` as the block below, matching (not
  shrinking) the operator's own confirmed default requests, purely for
  version-control visibility (an operator upgrade could silently change its
  injected defaults with nothing in git showing it). Deliberately **no
  `limits`** yet: the only real data available is `AWS_PLAN.md`'s Milestone
  3 note of ~712Mi observed for the *whole pod*, with no per-container
  split — setting a per-container memory limit without knowing that split
  risks a cgroup OOM-kill (`mongod` hard-crashes on exceeding `limits`,
  unlike merely exceeding `requests`, which only risks a softer kubelet
  eviction under real node pressure). Plan: bring the stack up with
  requests only, capture the real per-container split live
  (`kubectl top pod <mongo-pod> --containers`), then set limits from that
  data as a same-milestone follow-up, not guessed in advance.

  ```yaml
    statefulSet:
      spec:
        template:
          spec:
            containers:
              - name: mongod
                resources:
                  requests:
                    cpu: 500m
                    memory: 400Mi
              - name: mongodb-agent
                resources:
                  requests:
                    cpu: 500m
                    memory: 400Mi
  ```
- **`replicaCount=1`** for the ALB Controller itself — avoids adding
  avoidable memory pressure on top of an already-tight fleet; matches this
  project's existing single-replica precedent for infra pieces (one Kafka
  broker, one Connect replica).
- **User verifies the public ALB DNS name themselves** (curl/browser), not
  driven via browser automation — matches the Milestone 7 precedent.
- **Stack stays live at the end of this session** (not torn down) — matches
  the Milestone 8 precedent.

## Design

### 1. Terraform: new IRSA role + vendored IAM policy (`terraform/modules/iam/`)

Same shape as every existing IRSA role in the file: trust policy scoped via
`sts:AssumeRoleWithWebIdentity` to
`system:serviceaccount:kube-system:aws-load-balancer-controller`. Policy
attached is the vendored `alb-controller-policy.json` (downloaded via the
command above), read with `policy =
file("${path.module}/alb-controller-policy.json")` — a deliberate deviation
from the module's usual `aws_iam_policy_document` style, worth a one-line
comment: eliminates transcription risk on a ~200-line AWS-published policy
and keeps future diffs against upstream trivial.

New output: `alb_controller_role_arn`.

### 2. Helm install (CLI, not `helm_release`)

```bash
helm repo add eks https://aws.github.io/eks-charts
helm repo update
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=events-api-eks \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set replicaCount=1
```

Run only after the `ServiceAccount` below exists (step 3) — `serviceAccount.
create=false` means Helm expects it to already be there.

### 3. `k8s/overlays/aws-alb-controller/` — new overlay, cluster-scoped

Its own small overlay (not folded into `k8s/overlays/aws/`, which is
`events-api`-namespace app config) — one `ServiceAccount` in `kube-system`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: aws-load-balancer-controller
  namespace: kube-system
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::938500344309:role/events-api-iam-alb-controller-irsa
```

Applied standalone (`kubectl apply -k k8s/overlays/aws-alb-controller`)
before the Helm install in step 2.

### 4. `Ingress` resource (`k8s/overlays/aws/ingress.yaml`)

Added to the existing `aws` overlay's `kustomization.yaml`. References the
`realtime` Service by name only (a plain string, not a Kustomize resource
dependency) — the same cross-overlay-by-name pattern already used elsewhere
in this repo (e.g. `aws-cdc`'s connectors referencing `postgres:5432` by
hostname alone). Apply order matters: `k8s/overlays/aws-realtime` must
already be applied so the `realtime` Service exists.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: events-api
  namespace: events-api
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/healthcheck-path: /healthz
    alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=300
spec:
  ingressClassName: alb
  rules:
    - http:
        paths:
          - path: /stream
            pathType: Prefix
            backend:
              service:
                name: realtime
                port:
                  number: 3000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: events-api
                port:
                  number: 8000
```

`/stream` listed before `/` — required ordering, not style (see docs note
above). `idle_timeout.timeout_seconds=300` — `realtime` has no SSE
heartbeat, so a quiet stream would otherwise hit ALB's 60s default and look
like a flaky load balancer.

If `kubectl get ingressclass` doesn't show `alb` auto-created after the Helm
install, apply the documented fallback:

```yaml
apiVersion: networking.k8s.io/v1
kind: IngressClass
metadata:
  name: alb
spec:
  controller: ingress.k8s.aws/alb
```

### 5. Wake the full CDC/streaming stack

```bash
kubectl -n events-api scale deployment strimzi-cluster-operator --replicas=1
kubectl -n events-api scale deployment mongodb-kubernetes-operator --replicas=1
kubectl -n events-api scale deployment realtime --replicas=2
kubectl -n events-api scale deployment cdc-consumer --replicas=1
```

Strimzi's own operator then reconciles the untouched `Kafka`/`KafkaNodePool`/
`KafkaConnect` CRs (no `kubectl scale` needed on those — they're
operator-managed, reconciliation brings the broker/Connect pods up
automatically once the operator itself is running); the MongoDB Community
Operator similarly reconciles the untouched `MongoDBCommunity` CR. All 4
`KafkaConnector`s (2 Debezium sources, 2 BigQuery sinks) reconnect on their
own once Kafka Connect is up — no re-registration needed.

**Watch live, don't pre-judge**: `kubectl top nodes`, `kubectl top pods -A`
as things come up. If real usage (not requests) causes pressure: scale
`kube-prometheus-stack-grafana` (monitoring namespace) to zero first, then
`prometheus-kube-prometheus-stack-prometheus` (StatefulSet, monitoring) if
still needed. Leave `metrics-server`/`kube-state-metrics` untouched
regardless.

### 6. Set Mongo's resource limits from real per-container data

`mongodb-community.yaml` already carries a commented-out `resources.requests`
block for `mongod`/`mongodb-agent` (pinning the operator's own confirmed
defaults — see Decisions above), left inactive until real data is in hand.
Once the `MongoDBCommunity` CR has reconciled and the member pod is running:

```bash
kubectl -n events-api top pod -l app.kubernetes.io/name=mongodb --containers
```

Uncomment the `requests` block (matches current default behavior exactly, no
functional change — just makes it visible in git), then add a `limits` block
per container sized off the real per-container numbers just observed
(roughly 1.5-2x actual usage, matching this project's typical headroom
ratio elsewhere — `cdc-consumer` request-to-limit is exactly 2x). Do **not**
set a limit below observed usage — `mongod` hard-crashes (cgroup OOM-kill)
on exceeding `limits`, a materially worse failure than the soft eviction
risk of no limit at all.

## Verification (user-run)

**Phase 1 — ALB mechanism**, before waking the CDC stack:
```bash
curl http://<alb-dns-name>/                                          # real response from the Python app
curl -H "X-Tenant-ID: <uuid>" http://<alb-dns-name>/stream/events    # 503 — empty target group, expected
```

**Phase 2 — after waking the full stack**:
```bash
curl -N -H "X-Tenant-ID: <uuid>" http://<alb-dns-name>/stream/events
# in another shell:
curl -X POST http://<alb-dns-name>/events -H "X-Tenant-ID: <uuid>" -H "Content-Type: application/json" -d '{...}'
# the first curl should show the event arrive as real SSE data
```

This is the exact bar `AWS_PLAN.md`'s own Verification section states:
"the ALB's public DNS name serves both `/stream/events` (Node) and every
other route (Python) correctly, with no `port-forward` involved" — now
through the full, previously-built Milestone 3/4/5 pipeline rather than a
reduced slice of it.

## Teardown (documented now — not executed this session; stack stays live)

`terraform destroy` will **not** delete the ALB — the controller created it
in reaction to the `Ingress`, so Terraform has no record of it. Whenever
this does get torn down: `kubectl delete ingress events-api -n events-api`
first, confirm via `aws elbv2 describe-load-balancers` that the ALB and its
target groups actually disappear, *then* run `terraform destroy` —
otherwise orphaned controller-managed security groups/ENIs will likely
block the VPC deletion.

## Explicitly out of scope

- TLS/ACM/a real domain — captured as `AWS_PLAN.md` Milestone 13, a
  separate, optional follow-on with its own real cost.
- Multi-env (dev/stage/prod) subdomain routing — same Milestone 13, a
  distinct lesson from this milestone's single-environment path routing.
- Preemptively scaling down `metrics-server`/`kube-state-metrics` — stays a
  hard floor regardless of memory pressure (Milestone 7 HPA precedent).
- CI/CD automation of any of this — `AWS_PLAN.md` Milestone 10.

## Verification bar

From `AWS_PLAN.md`'s own Verification section: "Milestone 9: the ALB's
public DNS name serves both `/stream/events` (Node) and every other route
(Python) correctly, with no `port-forward` involved."
