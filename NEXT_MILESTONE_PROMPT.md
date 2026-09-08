# Next Session: AWS_PLAN.md Milestone 7

Start AWS_PLAN.md Milestone 7 (HPA, made genuinely verifiable). Read
`WHATS_NEXT.md` first for current state, then `AWS_PLAN.md`'s Milestone 7
section for scope. Before starting, confirm what's actually still live on
AWS (EKS/RDS may or may not have been torn down at the end of the last
session — check via `terraform state list` and live
`describe-cluster`/`describe-db-instances` calls, don't assume either way).

Scope: a small load-generation script actually drives the `events-api`
app's CPU up, so a real scale-up is observable via `kubectl get hpa` and the
existing Grafana dashboard (Milestone 6) — not configured-and-never-exercised.
Two real, confirmed facts from last session to build on rather than
rediscover: `events-api`'s Deployment **already has CPU requests set**
(`100m` request / `500m` limit — HPA can compute utilization off this with
no app-level gap to fix first), but **`metrics-server` does not exist on
this cluster at all** (confirmed via a real `kubectl get deploy`/`apiservice`
check, not assumed) — HPA's CPU-based scaling can't function without it, so
installing it is real, necessary prerequisite work for this milestone, the
same way porting the dbt CronJob to AWS turned out to be for Milestone 6.

**Capacity check comes first this time, before any implementation —
Milestone 6 lost most of its session to a capacity crisis discovered only
mid-implementation.** Known state to start from: the CDC/Mongo/realtime
stack (Strimzi Kafka, Kafka Connect, MongoDB, the consumer, the realtime
relay) is currently scaled to zero and should **stay that way** for this
milestone — it frees exactly the headroom a real HPA scale-up (more
`events-api` replicas) and `metrics-server`'s own footprint will need, and
nothing in this milestone's scope depends on any of it. The account's EC2
vCPU quota (8, confirmed hard-capped, already fully consumed by the
existing 4-node fleet before any of this milestone's work) hasn't changed —
check real per-node headroom live before assuming the load test's target
replica count is achievable, rather than hitting `VcpuLimitExceeded` again
mid-session. Milestone 6's Grafana dashboard already has a
`kube_deployment_status_replicas{deployment="events-api"}` panel (kept
specifically for this reason) — reuse it for the "observable via ... and
Grafana" verification bar rather than building a new one, unless an
HPA-specific panel (`kube_horizontalpodautoscaler_status_current_replicas`)
is wanted too.

**Start with `superpowers`'s `brainstorming` skill, not straight
implementation.** Installing `metrics-server` (a new cluster addon) plus a
new `HorizontalPodAutoscaler` resource and a load-generation script are
small individually, but the real capacity question above needs resolving
as part of the design, not discovered mid-`kubectl apply` — matching the
precedent both Milestone 5 and Milestone 6 already set of a written plan
before touching the cluster
(`docs/superpowers/plans/2026-09-08-aws-milestone-6-observability.md`).

Follow CLAUDE.md's hands-on teaching mode by default: explain what needs to
change and why, hand over the actual commands/edit content, let me run/apply
it myself, then verify afterward.

**Plugins to use this session:**
- `superpowers` — `brainstorming` → `writing-plans` before implementation
  (see above).
- `context7` — pull current docs for `metrics-server`'s official install
  manifest/Helm chart before wiring it in — EKS specifically often needs
  `--kubelet-insecure-tls` or equivalent due to how EKS's kubelet serving
  certs are set up, and that shouldn't be assumed from training data.
- `terraform` — likely not needed for `metrics-server` itself (installed via
  `kubectl apply`/Helm, matching this project's established no-Kubernetes-
  Terraform-provider convention), but check whether the load-generation
  script or HPA resource implies any new IAM/Terraform surface before
  assuming there's none.
- `aws-core` — its `aws-secrets-manager` skill's hook blocks direct
  `get-secret-value` calls; carries forward as a standing constraint even
  though this milestone likely doesn't touch secrets directly.
- `playwright` — visually confirm the Grafana replica-count panel actually
  moves during the load-generation script's run, matching Milestone 6's own
  verification bar (a real curl burst, visually confirmed against the
  dashboard) rather than only checking `kubectl get hpa`'s text output.
- `claude-security` — optional, offer a pass afterward given the new
  cluster-addon surface; not required to start.

Not relevant this milestone: `bigquery-data-analytics`, `mongodb`,
`frontend-design`, `claude-md-management`, `skill-creator`,
`commit-commands`, `warp`, `code-simplifier`.
