# AWS Milestone 9 (ALB Ingress) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work. Read-only diagnostics (`kubectl get`/`describe`/
> `top`, `aws ... describe-*`/`list-*`, `terraform plan`) are fine to run
> directly; anything mutating (`terraform apply`, `helm install`, `kubectl
> apply`/`scale`, file edits) is the user's to run.

**Goal:** Real external access to the cluster via the AWS Load Balancer
Controller, path-routing `/stream/*` to the Node SSE relay and everything
else to the Python app — replacing `kubectl port-forward` as the only way
in — verified against the full, previously-built Milestone 3/4/5 CDC
pipeline, not a reduced slice of it.

**Architecture:** A new IRSA role (zero-permission trust + the vendored
`AWSLoadBalancerControllerIAMPolicy`) for the controller's own
`kube-system` ServiceAccount. The controller itself installed via Helm CLI
(`replicaCount=1`), same as every other cluster-infra chart in this repo.
One `Ingress` in the existing `aws` overlay with two path rules
(`/stream` → `realtime:3000`, `/` → `events-api:8000`), `target-type: ip`
(forced — both backends are plain `ClusterIP`), a 300s idle timeout
(`realtime` has no SSE heartbeat), and one health-check path covering both
backends. Verified in two phases: the ALB mechanism alone first (while the
CDC stack is still at zero), then the full stack woken and `/stream/events`
re-verified through it.

**Tech Stack:** Terraform (`aws_iam_role`, `aws_iam_policy` from a vendored
JSON file), Helm CLI (`eks/aws-load-balancer-controller`), Kubernetes
`networking.k8s.io/v1` `Ingress`, the already-existing Strimzi/MongoDB
Community Operator CRs from Milestones 3/5.

**Spec:** `docs/superpowers/specs/2026-09-10-aws-milestone-9-alb-ingress-design.md`
— this plan implements that design directly; read both.

## Global Constraints

- `AWS_PROFILE=events-api-tf` for every AWS CLI call. Region `eu-central-1`,
  account `938500344309`, cluster `events-api-eks`.
- EKS and RDS are both confirmed live already (`ACTIVE`/`available`, checked
  this session) — no bootstrap needed, but run `terraform plan` before every
  `apply` and read the actual diff rather than assume it matches this plan's
  expected counts.
- **Bring the full CDC/streaming stack back, not a minimal subset** —
  `strimzi-cluster-operator` → `Kafka`/`KafkaConnect` CRs (all 4
  `KafkaConnector`s, including both BigQuery sinks) → `mongodb-kubernetes-operator`
  → the `MongoDBCommunity` CR → `cdc-consumer` → `realtime`. See the spec's
  Context section for why this supersedes `NEXT_MILESTONE_PROMPT.md`'s
  original minimal-subset framing.
- **Capacity is genuinely tight, not comfortable**: the spec's corrected math
  is 2480Mi needed against ~2460Mi free on the two eligible nodes
  (`ip-10-0-11-18`, `ip-10-0-11-186`) — hitting the reactive fallback (scale
  `kube-prometheus-stack-grafana` to zero first, then
  `prometheus-kube-prometheus-stack-prometheus` if still short) during Task 4
  is a real likely outcome, not a remote contingency. `metrics-server`/
  `kube-state-metrics` stay untouched regardless (Milestone 7 HPA
  precedent).
- **`k8s/overlays/aws-cdc/mongodb-community.yaml` already has a
  commented-out `resources.requests` block** for `mongod`/`mongodb-agent`
  (added by the user earlier this session, values already correct — pins
  the operator's own confirmed defaults). Task 5 activates it and adds
  `limits`; don't re-add it from scratch.
- **No TLS/ACM/domain anywhere in this milestone** — plain HTTP against the
  ALB's own DNS name. Captured separately as `AWS_PLAN.md` Milestone 13.
- Put nothing new on node `ip-10-0-10-232` — it's full on both memory (89%)
  and pod count (11/11, the hard per-node ceiling for `t4g.small`).
- Don't run `git commit` unless explicitly asked in that turn — the final
  task hands over the command, doesn't run it.
- The stack stays live at the end of this session (matches the Milestone 8
  precedent) — Task 6's teardown section is documentation only, not
  something to execute.

---

### Task 1: ALB Controller IRSA role + vendored IAM policy (Terraform)

**Files:**
- Create: `terraform/modules/iam/alb-controller-policy.json`
- Modify: `terraform/modules/iam/main.tf`
- Modify: `terraform/modules/iam/outputs.tf`
- Modify: `terraform/outputs.tf`

**Interfaces:**
- Produces: `alb_controller_role_arn` (root-level Terraform output),
  consumed by Task 2's `ServiceAccount` annotation.

- [ ] **Step 1: Download the current, AWS-published IAM policy**

```bash
cd terraform/modules/iam
curl -o alb-controller-policy.json \
  https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/main/docs/install/iam_policy.json
```

Vendored rather than hand-translated into `aws_iam_policy_document` — it's
a ~200-line, AWS-published policy; copying it verbatim eliminates
transcription risk and keeps future diffs against upstream trivial. Confirm
it downloaded as real JSON, not an HTML error page:

```bash
python3 -m json.tool alb-controller-policy.json > /dev/null && echo "valid JSON"
```

- [ ] **Step 2: Add the IRSA role, policy, and attachment**

At the end of `terraform/modules/iam/main.tf` (after the existing
`aws_iam_role.k8s_viewer` block), add:

```hcl
# The ALB Controller's IRSA role — real AWS permissions attached (unlike
# k8s_viewer/kafka_connect_gcp_irsa above), since this identity actually
# calls the EC2/ELB APIs to create and manage real load balancers.
data "aws_iam_policy_document" "alb_controller_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:kube-system:aws-load-balancer-controller"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "alb_controller_irsa" {
  name               = "${var.name_prefix}-alb-controller-irsa"
  assume_role_policy = data.aws_iam_policy_document.alb_controller_irsa_trust.json
}

resource "aws_iam_policy" "alb_controller" {
  name   = "${var.name_prefix}-alb-controller-policy"
  policy = file("${path.module}/alb-controller-policy.json")
}

resource "aws_iam_role_policy_attachment" "alb_controller_irsa" {
  role       = aws_iam_role.alb_controller_irsa.name
  policy_arn = aws_iam_policy.alb_controller.arn
}
```

No new module variables needed — unlike Milestone 8's `k8s_viewer` role,
this one only needs `var.oidc_provider_arn`/`var.oidc_provider_url`, both
already passed into `modules/iam` from root `main.tf`.

- [ ] **Step 3: Output the role ARN**

At the end of `terraform/modules/iam/outputs.tf`, add:

```hcl
output "alb_controller_role_arn" {
  description = "IRSA role ARN for the AWS Load Balancer Controller's kube-system ServiceAccount"
  value       = aws_iam_role.alb_controller_irsa.arn
}
```

At the end of root `terraform/outputs.tf`, add:

```hcl
output "alb_controller_role_arn" {
  description = "IRSA role ARN for the AWS Load Balancer Controller's kube-system ServiceAccount"
  value       = module.iam.alb_controller_role_arn
}
```

- [ ] **Step 4: Plan, review, apply**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform plan
```

Expect exactly 3 resources to add: `aws_iam_role.alb_controller_irsa`,
`aws_iam_policy.alb_controller`, `aws_iam_role_policy_attachment.alb_controller_irsa`
(the new `data` source doesn't count toward the total — it's a local
computation, not an API-backed resource). Read the actual printed count
before applying rather than assuming it matches.

```bash
AWS_PROFILE=events-api-tf terraform apply
```

- [ ] **Step 5: Confirm the output**

```bash
terraform output -raw alb_controller_role_arn
```

Expected: `arn:aws:iam::938500344309:role/events-api-iam-alb-controller-irsa`.
Keep this value — Task 2 needs it.

---

### Task 2: ServiceAccount + Helm install of the AWS Load Balancer Controller

**Files:**
- Create: `k8s/overlays/aws-alb-controller/kustomization.yaml`
- Create: `k8s/overlays/aws-alb-controller/service-account.yaml`

**Interfaces:**
- Consumes: `alb_controller_role_arn` from Task 1 Step 5.
- Produces: a running controller watching for `Ingress` objects with
  `ingressClassName: alb`, consumed by Task 3.

- [ ] **Step 1: The ServiceAccount, in its own small overlay**

Create `k8s/overlays/aws-alb-controller/service-account.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: aws-load-balancer-controller
  namespace: kube-system
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::938500344309:role/events-api-iam-alb-controller-irsa
```

Create `k8s/overlays/aws-alb-controller/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - service-account.yaml
```

A standalone overlay (no `../../base`) — this is `kube-system`-scoped
cluster infra, not `events-api`-namespace app config, so it doesn't layer
on the app base the way `k8s/overlays/aws/` does.

- [ ] **Step 2: Apply it, before the Helm install**

```bash
export AWS_PROFILE=events-api-tf
aws eks update-kubeconfig --name events-api-eks --region eu-central-1
kubectl apply -k k8s/overlays/aws-alb-controller
```

Expect `serviceaccount/aws-load-balancer-controller created`. Confirm the
annotation landed correctly:

```bash
kubectl -n kube-system get serviceaccount aws-load-balancer-controller -o jsonpath='{.metadata.annotations}'
```

Expected: `{"eks.amazonaws.com/role-arn":"arn:aws:iam::938500344309:role/events-api-iam-alb-controller-irsa"}`.

- [ ] **Step 3: Helm install the controller**

```bash
helm repo add eks https://aws.github.io/eks-charts
helm repo update
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  -f helm/aws-load-balancer-controller/values-override.yaml
```

Follows this project's own established convention (`helm/bitnami-postgres/values-override.yaml`, `helm/metrics-server/values-override.yaml`, `helm/kube-prometheus-stack/values-override.yaml`) — a real, committed values file instead of `--set` flags scattered across a command line, so every value (including the IMDS `region`/`vpcTags` workaround and the resource requests/limits — the chart leaves these completely unbounded if unset) survives a fresh install exactly as-is, not just this one session's live `helm upgrade` history.

`serviceAccount.create=false` — Helm expects the ServiceAccount from Step 1
to already exist, which it now does. `replicaCount=1` overrides the chart's
HA-by-default 2 replicas — avoids adding avoidable memory pressure
(200Mi/replica) on top of an already-tight fleet.

- [ ] **Step 4: Confirm the controller is running**

```bash
kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=120s
kubectl -n kube-system get pods -l app.kubernetes.io/name=aws-load-balancer-controller
```

Expected: 1 pod, `Running`, `1/1 Ready`. If the label selector returns
nothing (unverified against this exact chart version), fall back to
`kubectl -n kube-system get pods | grep aws-load-balancer-controller`. If it's stuck `Pending` or
`CrashLoopBackOff`, check `kubectl -n kube-system describe pod
<pod-name>` for the real reason before proceeding — a common cause here
would be `ImagePullBackOff` if the image somehow lacked `arm64` support
(every node in this cluster is `t4g`/Graviton), though the controller's
`public.ecr.aws` images are multi-arch as a matter of course.

- [ ] **Step 5: Check whether the `alb` IngressClass was auto-created**

```bash
kubectl get ingressclass
```

If `alb` appears in the list, move on to Task 3. If it doesn't (the
chart's exact auto-creation toggle wasn't pinned down against current
docs — see the spec), apply the documented fallback:

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: IngressClass
metadata:
  name: alb
spec:
  controller: ingress.k8s.aws/alb
EOF
```

---

### Task 3: The `Ingress` resource + Phase 1 verification (ALB mechanism only)

**Files:**
- Create: `k8s/overlays/aws/ingress.yaml`
- Modify: `k8s/overlays/aws/kustomization.yaml`

**Interfaces:**
- Consumes: the `events-api` and `realtime` Services (both already live —
  confirmed this session via `kubectl -n events-api get service
  events-api realtime`), and the `alb` `IngressClass` from Task 2.
- Produces: a real, publicly-reachable ALB DNS name, verified in this task
  against the Python app route only — `realtime` is still at 0 replicas,
  so `/stream/events` is expected to 503 here, not succeed.

- [ ] **Step 1: Create the Ingress manifest**

Create `k8s/overlays/aws/ingress.yaml`:

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

`/stream` listed **before** `/` — the controller assigns ALB listener-rule
priority by list position; a catch-all `/` listed first would swallow
`/stream/events` too. `target-type: ip` is forced, not chosen — both
backend Services are plain `ClusterIP` with no `NodePort`. The 300s idle
timeout exists because `realtime/src/index.ts` has no periodic SSE
heartbeat — a quiet stream would otherwise hit ALB's 60s default and look
like a flaky load balancer rather than a config default.

- [ ] **Step 2: Wire it into the `aws` overlay**

In `k8s/overlays/aws/kustomization.yaml`, the `resources:` list currently
reads:

```yaml
resources:
  - ../../base
  - service-account.yaml
  - migration-service-account.yaml
  - hpa.yaml
  - viewer-service-account.yaml
  - viewer-rbac.yaml
```

Add the new file:

```yaml
resources:
  - ../../base
  - service-account.yaml
  - migration-service-account.yaml
  - hpa.yaml
  - viewer-service-account.yaml
  - viewer-rbac.yaml
  - ingress.yaml
```

- [ ] **Step 3: Dry-run render before touching the cluster**

```bash
kubectl kustomize k8s/overlays/aws | grep -A30 "kind: Ingress"
```

Confirm the rendered `Ingress` has both rules in the right order (`/stream`
before `/`), the four annotations, and `namespace: events-api` — the same
dry-run discipline this project has used at every prior Kustomize step.

- [ ] **Step 4: Apply**

```bash
kubectl apply -k k8s/overlays/aws
```

Expect `ingress.networking.k8s.io/events-api created` — every other line
should read `unchanged`, confirming nothing else in the overlay drifted.

- [ ] **Step 5: Wait for the ALB to provision, get its DNS name**

```bash
kubectl get ingress events-api -n events-api -w
```

ALB provisioning takes a couple of minutes. Watch until the `ADDRESS`
column populates with a real `*.elb.amazonaws.com` hostname, then Ctrl-C.
If it's still empty after ~5 minutes, check events for the real reason:

```bash
kubectl describe ingress events-api -n events-api
```

Save the DNS name — every verification step below needs it.

- [ ] **Step 6: Phase 1 verification — Python route works, `/stream` correctly empty**

```bash
export ALB_DNS="<the address from Step 5>"
curl -i "http://${ALB_DNS}/"
```

Expected: a real HTTP response from the Python app (not a connection
error, not a 5xx from the ALB itself).

```bash
curl -i -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" "http://${ALB_DNS}/stream/events"
```

Expected: `503` — the `realtime` target group is registered but has zero
healthy targets (it's still scaled to 0 replicas). This is the expected,
correct state at this point, not a failure — it proves the ALB mechanism
itself (IRSA, target-group registration, health checks, rule routing) works
end-to-end before any CDC-stack complexity is introduced.

---

### Task 4: Wake the full CDC/streaming stack + Phase 2 verification

**Files:** none (cluster-side scale + verification only)

**Interfaces:**
- Consumes: the untouched `Kafka`/`KafkaNodePool`/`KafkaConnect`/
  `MongoDBCommunity` CRs from Milestones 3/5, and all 4 already-registered
  `KafkaConnector` CRs.
- Produces: real CDC events reaching `realtime`'s SSE stream via the ALB,
  the milestone's actual verification bar.

- [ ] **Step 1: Scale the operators and the two plain Deployments back up**

```bash
kubectl -n events-api scale deployment strimzi-cluster-operator --replicas=1
kubectl -n events-api scale deployment mongodb-kubernetes-operator --replicas=1
kubectl -n events-api scale deployment realtime --replicas=2
kubectl -n events-api scale deployment cdc-consumer --replicas=1
```

Strimzi's operator then reconciles the untouched `Kafka`/`KafkaNodePool`/
`KafkaConnect` CRs on its own (no `kubectl scale` needed on those — they're
operator-managed); the MongoDB Community Operator similarly reconciles the
untouched `MongoDBCommunity` CR. All 4 `KafkaConnector`s (2 Debezium
sources, 2 BigQuery sinks) reconnect automatically once Kafka Connect is
up — nothing needs re-registering.

- [ ] **Step 2: Watch reconciliation and real capacity together, live**

```bash
kubectl -n events-api get pods -w
```

Watch until `strimzi-cluster-operator`, the Kafka broker pod, Kafka
Connect's pod, `mongodb-kubernetes-operator`, the Mongo member pod,
`cdc-consumer`, and both `realtime` pods are all `Running`/`Ready`. In
another shell, watch real usage as it happens:

```bash
watch -n 5 'kubectl top nodes; echo; kubectl top pods -A --sort-by=memory | head -20'
```

**If a pod sits `Pending` with `FailedScheduling: Insufficient memory`**
(the spec's corrected capacity math puts this at genuinely likely, not a
remote edge case): scale Grafana down first, then Prometheus if still
short —

```bash
kubectl -n monitoring scale deployment kube-prometheus-stack-grafana --replicas=0
# only if still insufficient after Grafana:
kubectl -n monitoring scale statefulset prometheus-kube-prometheus-stack-prometheus --replicas=0
```

Leave `metrics-server`/`kube-state-metrics` untouched regardless of
pressure — losing `metrics-server` specifically regresses Milestone 7's
proven HPA back to `<unknown>` targets, for negligible capacity gain.

- [ ] **Step 3: Confirm the CRs themselves report ready**

```bash
kubectl -n events-api get kafka,kafkaconnect,kafkaconnector,mongodbcommunity
```

Expected: the `Kafka` and `KafkaConnect` custom resources show `READY:
True`; all 4 `KafkaConnector`s show `READY: True` (or blank momentarily
right after Connect starts — recheck after ~30s if so); the
`MongoDBCommunity` resource (`events-mongo`) shows phase `Running`.

- [ ] **Step 4: Phase 2 verification — real CDC event through the ALB's `/stream/events`**

In one shell, open the stream:

```bash
curl -N -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" "http://${ALB_DNS}/stream/events"
```

In a second shell, post a real event for that same tenant:

```bash
curl -X POST "http://${ALB_DNS}/events" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -H "Content-Type: application/json" \
  -d '{"event_type": "milestone_9_verification", "user_id": "verify-user", "properties": {}}'
```

Expected: the first shell's still-open `curl -N` prints a real
`data: {...}` SSE line containing that event within a few seconds — the
literal bar `AWS_PLAN.md`'s own Verification section states for this
milestone, now proven through the full CDC pipeline and the ALB instead of
`port-forward`.

---

### Task 5: Mongo resource limits from real per-container data

**Files:**
- Modify: `k8s/overlays/aws-cdc/mongodb-community.yaml`

**Interfaces:**
- Consumes: the running Mongo member pod from Task 4.

- [ ] **Step 1: Find the pod name, then capture real per-container usage**

The exact label the MongoDB Community Operator applies to its pods hasn't
been verified live in this plan (Mongo's been at 0 replicas all session) —
don't guess a label selector. Find it directly instead:

```bash
kubectl -n events-api get pods | grep mongo
```

The CR is named `events-mongo` (`mongodb-community.yaml`'s
`metadata.name`); the StatefulSet the operator generates from it uses that
same name, so the member pod should be `events-mongo-0` (standard
Kubernetes StatefulSet naming — `<statefulset-name>-<ordinal>`). Confirm
the actual name from the command above rather than assume it, then:

```bash
kubectl -n events-api top pod events-mongo-0 --containers
```

(substitute the real pod name from the `grep` above if it differs). Record
the actual `mongod` and `mongodb-agent` CPU/memory numbers printed — these
are the real numbers Step 2 below is built from, not the ~712Mi combined
figure from `AWS_PLAN.md`'s Milestone 3 note, which was pod-total, not
per-container.

- [ ] **Step 2: Uncomment the requests block, add limits sized from Step 1's real data**

The file currently has (added earlier this session):

```yaml
  statefulSet:
    spec:
      # template:
      #   spec:
      #     containers:
      #       - name: mongod
      #         resources:
      #           requests:
      #             cpu: 500m
      #             memory: 400Mi
      #       - name: mongodb-agent
      #         resources:
      #           requests:
      #             cpu: 500m
      #             memory: 400Mi
```

Uncomment it (no functional change yet — matches the operator's existing
default behavior exactly), then add a `limits` block to each container.
Decision procedure, not a guess: take each container's real memory usage
from Step 1 and multiply by 1.5-2x for headroom (matching this project's
existing ratio elsewhere — `cdc-consumer`'s request-to-limit ratio is
exactly 2x). For example, if Step 1 showed `mongod` at 280Mi and
`mongodb-agent` at 90Mi actual usage, the result would be:

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
                limits:
                  cpu: 1000m
                  memory: 560Mi
            - name: mongodb-agent
              resources:
                requests:
                  cpu: 500m
                  memory: 400Mi
                limits:
                  cpu: 1000m
                  memory: 256Mi
```

(Those specific `limits` values are illustrative, computed from the
illustrative 280Mi/90Mi example above — replace them with 1.5-2x of
whatever Step 1 actually printed.) Keep the `requests` values as-is
(500m/400Mi each — already confirmed to match the operator's real
defaults). Never set a `limits.memory` below the real observed usage from
Step 1 — `mongod` hard-crashes via cgroup OOM-kill on exceeding `limits`,
a materially worse failure than the soft kubelet-eviction risk of no limit
at all.

- [ ] **Step 3: Re-apply and confirm the new pod picks it up**

```bash
kubectl apply -k k8s/overlays/aws-cdc
kubectl -n events-api get pods -w | grep mongo
```

The MongoDB Community Operator performs a rolling restart of the member
pod to apply the new container spec. Watch until the pod (same name found
in Step 1, e.g. `events-mongo-0`) is `Running`/`Ready` again, then confirm
the applied values:

```bash
kubectl -n events-api get pod events-mongo-0 -o jsonpath='{.spec.containers[*].resources}'
```

Expected: the `requests`/`limits` you just wrote, for both `mongod` and
`mongodb-agent`.

---

### Task 6: Document real results, commit, teardown reminder

**Files:**
- Modify: `WHATS_NEXT.md`

- [ ] **Step 1: Record the real, observed results**

Add a new entry under the Milestone 8 entry in `WHATS_NEXT.md`, following
that file's existing style. Include, as actually observed (not the
expected values from this plan restated as if they were the result):

- The real ALB DNS name and both `curl` outputs from Task 3 Step 6 and
  Task 4 Step 4.
- Whether the Grafana/Prometheus reactive fallback was actually needed
  during Task 4 Step 2, and which of the two (or both) got scaled down.
- The real per-container Mongo usage from Task 5 Step 1 and the limits
  values actually chosen.
- Any real bug hit and fixed along the way, per this project's own
  established documentation habit.

- [ ] **Step 2: Hand over the final commit**

```bash
git add terraform/modules/iam/main.tf terraform/modules/iam/outputs.tf \
  terraform/modules/iam/alb-controller-policy.json terraform/outputs.tf \
  k8s/overlays/aws-alb-controller/ \
  k8s/overlays/aws/ingress.yaml k8s/overlays/aws/kustomization.yaml \
  k8s/overlays/aws-cdc/mongodb-community.yaml \
  WHATS_NEXT.md
git commit -m "$(cat <<'EOF'
Milestone 9: ALB Ingress, path-routed to both backends

Real external access via the AWS Load Balancer Controller (new IRSA role +
vendored IAM policy, Helm-installed, replicaCount=1) replacing
kubectl port-forward as the only way into the cluster. One Ingress
routes /stream/* to the Node SSE relay and everything else to the Python
app. Verified in two phases: the ALB mechanism alone, then a real CDC
event flowing end-to-end through /stream/events after waking the full
Milestone 3/5 CDC/streaming stack (not a reduced subset). Mongo's
container resources pinned explicitly from real observed per-container
usage rather than left as an invisible operator default.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KZfktKJ1d5yT7Y6XQ594AW
EOF
)"
```

Per this repo's own discipline: don't run this commit unless explicitly
asked to in this turn.

- [ ] **Step 3: Teardown reminder, not part of this task's deliverable**

`terraform destroy` will **not** delete the ALB — the controller created it
in reaction to the `Ingress`, so Terraform has no record of it. Whenever
this does get torn down: `kubectl delete ingress events-api -n events-api`
first, confirm via `aws elbv2 describe-load-balancers` that the ALB and its
target groups actually disappear, *then* run `terraform destroy` —
otherwise orphaned controller-managed security groups/ENIs will likely
block the VPC deletion. The IAM role (`modules/iam`) persists across the
per-milestone EKS/networking destroy cycle, same as every other IRSA role
here — nothing to clean up there. Confirm with the user whether to tear
down now or leave everything live for the next milestone, per this
project's standing per-milestone teardown discipline — this session's own
decision was to leave it live.
