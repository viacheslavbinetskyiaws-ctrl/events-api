# Next Session: AWS_PLAN.md Milestone 9

Start AWS_PLAN.md Milestone 9 (ALB Ingress). Read `WHATS_NEXT.md` first for
current state (the Milestone 8 entry has the real RBAC story — a new
least-privilege IAM role mapped through an EKS access entry's
`kubernetes_groups`, verified two ways via `kubectl auth can-i`, with
`terraform-events-api`/`root`'s existing cluster-admin access deliberately
left untouched), then `AWS_PLAN.md`'s Milestone 9 section for scope: real
external access via the AWS Load Balancer Controller, path-based routing to
**both** backends (`/stream/*` → the Node SSE service from Milestone 4,
everything else → the Python app) — replacing `kubectl port-forward` as the
only way into this project, which has been faked "production readiness"
this entire time.

Before starting, confirm what's actually still live on AWS the same way
Milestone 8 did — EKS/RDS were both still `ACTIVE`/`available` at the start
of the Milestone 8 session (confirmed live via
`describe-cluster`/`describe-db-instances`), and stayed untouched all the
way through Milestone 8 (per the user's explicit choice to leave them live
rather than tear down at session end) — but check again rather than trust
that a session boundary didn't change anything.

**Real state checked at the end of the Milestone 8 session, to build on
rather than rediscover:**

- **The CDC/Mongo/realtime stack is still scaled to zero** — confirmed live
  via `kubectl -n events-api get deployment realtime
  strimzi-cluster-operator mongodb-kubernetes-operator` (all `0/0/0`) and
  `kubectl -n events-api get pods` (no Kafka/Mongo pods at all, only
  completed Jobs and the one running `events-api` pod). This is a **real
  blocker for this milestone's own verification bar**, not a side note:
  `AWS_PLAN.md` requires the ALB to actually serve `/stream/events`
  correctly, which needs the `realtime` Deployment running with at least
  one ready replica — and `realtime` needs Kafka reachable to avoid
  crash-looping on startup (confirmed in Milestone 4's own work: it
  subscribes to `cdc.public.events`/`cdc.public.tenant_accounts` at boot).
  - **Bring up the minimal subset, not the whole stack.** This milestone's
    verification only needs real CDC events reaching the `realtime`
    relay's SSE stream — that needs Kafka broker + Kafka Connect (with the
    Debezium *source* connectors registered, so real change events land on
    the topics) + `realtime` itself. It does **not** need MongoDB, the
    Python `streaming/consumer.py` process, or the BigQuery sink
    connectors — none of those feed the SSE path, and skipping them keeps
    the memory footprint meaningfully below what forced Milestone 6's
    stack-to-zero fix in the first place. Bring up
    `strimzi-cluster-operator` first (it reconciles the untouched `Kafka`/
    `KafkaNodePool`/`KafkaConnect` CRs), then `realtime` — skip
    `mongodb-kubernetes-operator` and the consumer entirely unless
    something in this session's own scoping decides otherwise.
  - **Known hard ceiling, unrelated to memory**: the account's 8-vCPU
    node-group quota (Milestone 6) is already fully consumed by the
    existing 4-node fleet (3 default + 1 dedicated Kafka Connect,
    confirm the latter's taint/existence is still live) — there is no
    5th node available regardless of what fits in memory. Check real
    per-node headroom live (`kubectl top nodes`, `kubectl describe hpa`
    for `events-api`'s own footprint) once the minimal subset is up,
    rather than assume Milestone 3's original sizing still holds now that
    kube-prometheus-stack (Milestone 6) and `metrics-server` (Milestone 7)
    also share these same nodes.
  - **If it still doesn't fit, the fallback order is Grafana, then
    Prometheus — not Mongo/the consumer (already skipped above) and not
    `metrics-server`.** Scale `kube-prometheus-stack-grafana`
    (`monitoring` namespace) to zero first — pure UI, nothing depends on
    it, trivially reversible. If that alone isn't enough, scale the
    `prometheus-kube-prometheus-stack-prometheus` StatefulSet
    (`monitoring`) down next — scraping just pauses, nothing corrupts.
    Leave `metrics-server`/`kube-state-metrics` alone regardless — both
    are already lightweight, and losing `metrics-server` specifically
    would regress Milestone 7's own proven HPA back to reporting
    `<unknown>` targets for negligible capacity gain. This is a fallback
    *order* to reach for only if the minimal subset genuinely doesn't fit
    — not something to do preemptively before checking real numbers.
- **VPC subnets are already tagged for this milestone, from Milestone 1** —
  `kubernetes.io/role/elb` (public subnets) / `kubernetes.io/role/internal-elb`
  (private subnets), added proactively specifically because "retrofitting
  later means another apply cycle on a torn-down VPC." This is a
  found-already-covered item, not new work — confirm the tags are still
  present live (`aws ec2 describe-subnets`) rather than assume they
  survived every intervening milestone unchanged, but don't re-do this
  work if they're already there.
- **No AWS Load Balancer Controller exists yet, and no IRSA role for it
  exists yet** — this project's IRSA precedent so far (EBS CSI driver,
  Milestone 3) used a Terraform-native `aws_eks_addon`; the ALB Controller
  has no such native-addon path and needs Helm instead, per `AWS_PLAN.md`'s
  own text — but the **IAM role + policy** it needs (the well-known,
  lengthy `AWSLoadBalancerControllerIAMPolicy` JSON AWS publishes) is still
  Terraform, in `modules/iam/`, same as every other IRSA role in this
  project. Pull that policy JSON from AWS's own current docs at
  implementation time (`context7`/`WebFetch`), not from training-data
  memory — it's large, specific, and has changed shape across controller
  versions.
- **No `Ingress` resource, no ALB annotations, exist anywhere in this repo
  yet** (grepped `k8s/` for `alb.ingress.kubernetes.io`/`ingressClassName`
  — none found). The exact annotation set for path-based routing across two
  backend Services (scheme, target-type, health-check paths per backend)
  needs verifying against the controller's own current docs before writing
  it, not assumed from a remembered example.
- **TLS/ACM is not named anywhere in `AWS_PLAN.md`'s Milestone 9 scope** —
  this project has no auth anywhere by design, and every other endpoint
  built so far has been plain HTTP. Don't add a certificate/HTTPS listener
  unless a deliberate scope decision this session adds it — check with the
  user rather than assume either way.
- Milestone 4's Node service and its `k8s/overlays/aws-realtime/` manifests
  are already built and were already proven end-to-end (byte-identical SSE
  fan-out across 2 pods) — nothing to rebuild there, only to route to once
  it's scaled back up.

**Start with `superpowers`'s `brainstorming` skill, not straight
implementation** — same precedent as Milestones 6, 7, and 8's own written
plans (`docs/superpowers/plans/`). This milestone's scope is more
concretely specified up front than Milestone 8's was (`AWS_PLAN.md` already
names the exact routing shape), but real design surface still exists: the
exact ALB annotation set, per-backend health-check configuration, and
whether waking the CDC stack back up needs to happen as its own step before
or as part of the Ingress work.

Follow CLAUDE.md's hands-on teaching mode by default: explain what needs
to change and why, hand over the actual commands/edit content, let me
run/apply it myself, then verify afterward.

**Plugins to use this session:**
- `superpowers` — `brainstorming` → `writing-plans` before implementation
  (see above).
- `terraform` — needed: a new IRSA role + the `AWSLoadBalancerControllerIAMPolicy`
  policy document in `modules/iam/`, following the existing IRSA pattern
  (trust scoped to the controller's own ServiceAccount, e.g.
  `system:serviceaccount:kube-system:aws-load-balancer-controller`).
- `context7` — pull current docs for the AWS Load Balancer Controller's
  Helm install (chart name/repo, required values) and its `Ingress`
  annotation reference (path-based routing across multiple backends,
  health-check annotations) before writing either — both have changed
  shape across versions and shouldn't be assumed from training data.
- `aws-core` — its `aws-iam` skill covers the new IRSA trust-policy
  correctness; its `aws-secrets-manager` skill's hook still carries forward
  as a standing constraint even though this milestone doesn't touch
  secrets directly.
- `playwright`/`claude-in-chrome` — likely useful this time, unlike
  Milestone 7/8: the actual verification bar is hitting the ALB's real
  public DNS name from a browser/`curl` for both routes, which is exactly
  the kind of external-facing check these are suited for. Milestone 7
  established that the user prefers checking things like Grafana in their
  own browser rather than Claude driving it for a simple confirmation —
  ask before assuming which mode applies here, since this is the first
  milestone where the thing being checked is a public endpoint rather than
  an internal dashboard.
- `claude-security` — optional, offer a pass afterward given the new
  public-facing ALB and IAM surface; not required to start.

Not relevant this milestone: `bigquery-data-analytics`, `mongodb`,
`frontend-design`, `claude-md-management`, `skill-creator`,
`commit-commands`, `warp`, `code-simplifier`.
