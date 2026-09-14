# Next Session: AWS_PLAN.md Milestone 11

Start AWS_PLAN.md Milestone 11 (shrink storage to real minimums, deferred
from Milestone 5 — now also covering `vpc-cni`/`kube-proxy` addon adoption,
added 2026-09-14, see below). Read `WHATS_NEXT.md` first for full current
state — Milestone 10's entry has the real CI/CD story (nine real bugs
getting GitHub Actions' OIDC federation working, most in the trust-policy
surface itself), and the dated 2026-09-14 follow-up entry right after it
covers a real capacity audit, a genuine Kafka Connect memory root-cause fix,
and a full redesign of the `deploy` workflow — all relevant background, not
just Milestone 10 itself. Then read `AWS_PLAN.md`'s Milestone 11 section for
full scope: RDS `allocated_storage` destroyed/recreated from `50`/`gp2`/
`max_allocated_storage=100` down to the real verified minimum (`5` GiB on
`db.t4g.micro`/postgres18/`gp2`), the Kafka/Mongo EBS PVCs (currently
`5Gi`/`2Gi`+`1Gi`) shrunk to their real EBS floor (`1Gi` each), and — new —
adopting `vpc-cni`/`kube-proxy` as proper Terraform-managed `aws_eks_addon`
resources with real declared requests, matching the `ebs_csi` addon's
existing pattern.

Before starting, confirm what's actually still live on AWS the same way
every prior milestone has — EKS/RDS were both still `ACTIVE`/`available` as
of 2026-09-14 (confirmed via `describe-cluster`/`describe-db-instances`, RDS
storage still at the pre-shrink `50` GiB), all pods across all 3 namespaces
`Running`/`Completed`, nothing crash-looping — but check again rather than
trust that a session boundary didn't change anything.

**Real usage numbers, verified 2026-09-14** — all three storage targets
confirm the same story, real usage nowhere near even the shrunk-to
minimums: RDS **~3.5GiB used of 50GiB allocated** (~7%, via CloudWatch
`FreeStorageSpace`) against a verified `5GiB` real minimum; Kafka PVC
**92Mi used of 5Gi** (2%); Mongo data-volume **386Mi used of 2Gi** (20%);
Mongo logs-volume **69Mi used of 1Gi** (8%) — against a verified `1Gi` EBS
floor for all three. CPU is a non-issue cluster-wide (1-5% node
utilization; every pod checked uses a small fraction of its own CPU
*request*) and — worth remembering rather than re-deriving — **reducing
CPU requests would not reduce the AWS bill at all**: this project runs
fixed-size EC2 node groups, not Fargate, so AWS bills for the 4 provisioned
`t4g.small` instances regardless of what pods request; only changing
`desired_size`/instance type in `terraform/modules/eks/main.tf` would move
that number, and that's not realistically achievable right now regardless.

**Node capacity, as of the end of the 2026-09-14 follow-up session** (check
fresh, don't trust this number to have held): `ip-10-0-11-27` (Kafka
Connect's dedicated node) recovered from 96-99% to ~80% real memory after a
real root-cause fix (below). `ip-10-0-11-18` is still tight at ~97% real
memory — untouched by that fix, driven mostly by the Kafka broker
(`events-dual-role-0`, 720Mi request, the single largest consumer there)
plus real `aws-node`/`kube-proxy` usage the scheduler can't see (no declared
requests — exactly the gap this milestone's new addon-adoption scope
addresses). Since this milestone's Kafka PVC resize forces that broker pod
to reschedule anyway, that's the more likely real relief for this node —
not something to force separately.

**Real, root-caused fix already applied this cycle, don't rediscover it**:
Kafka Connect's real memory had grown to 978Mi (only 46Mi below its 1Gi
limit) because Strimzi auto-computes `-Xmx` as 75% of the container memory
limit when unset, and G1GC never releases committed heap back to the OS —
so Milestone 9's own limit increase had silently raised the JVM's own heap
ceiling too, and it grew into it over the following days. Fixed by pinning
`-Xmx`/`-Xms` to `384m` in `k8s/overlays/aws-cdc/kafka-connect.yaml`
(Strimzi JVM options use JDK unit conventions — `m`/`g`, not Kubernetes'
`Mi`/`Gi`), based on real measured live heap usage, not a guess. Verified:
real RSS dropped to ~690Mi, the hosting node recovered to ~80%. If Kafka
Connect's memory ever creeps back up, this is a heap-ceiling problem to
investigate the same way (`jcmd VM.native_memory summary`, not a guess),
not a "raise the limit again" problem — that's exactly what didn't work
the first time.

**`deploy` was fully redesigned this cycle — re-read `ci.yaml` fresh,
don't assume the Milestone 10 shape still applies**: it no longer takes an
`image_tag` input. `build-push` now pushes both `:latest` and the SHA tag
on every build; k8s manifests permanently reference `:latest`; `deploy` is
a plain `kubectl rollout restart` (the only thing that actually forces a
re-pull — a running pod never re-pulls on its own just because a new image
landed on the same tag) covering `events-api`, `realtime`, and
`cdc-consumer`. This was a real fix for a self-inflicted regression: the
original SHA-based design meant `deploy`'s live-only `kubectl set image`
patch could be silently reverted by any later `kubectl apply -k` on the
same manifests (it happened for real this cycle). **One real limitation
found and deliberately not fixed**: `deploy` still can't safely run a full
`kubectl apply -k` itself — `k8s/overlays/aws` (which every CDC/realtime
overlay chains through) contains the RBAC `Role`/`RoleBinding` objects
themselves, and granting an automated CI identity write access to RBAC is
a genuine privilege-escalation anti-pattern, deliberately excluded from
Kubernetes' built-in `edit` role and AWS's `EditPolicy` alike. So anything
beyond the 3 Deployments `deploy` restarts (Kafka Connect config changes,
RBAC changes, new PVC sizes, the new addon changes this milestone adds)
still needs a manual, locally-run `kubectl apply -k`/`terraform apply` —
same as this session did for all of today's fixes.

**This is the first real exercise of the Terraform CI/CD pipeline Milestone
10 built — that's the whole reason the user wanted this milestone picked
up next.** The RDS storage change and the new `vpc-cni`/`kube-proxy`
addons are both normal Terraform diffs and should flow through the real
pipeline exactly as designed: a PR touching `terraform/**` → `plan` runs
automatically, read its output for real before merging → merge → manually
trigger `apply` via `workflow_dispatch` (Actions → Terraform → Run
workflow — no `gh` CLI installed as of this writing, use the web UI unless
that's changed). Don't fall back to a local `terraform apply` for these
specifically unless the pipeline itself is genuinely broken — that would
defeat the actual point of doing this milestone now rather than later.

**The Kafka/Mongo PVC resize is separate, plain `kubectl` work, not
Terraform** — `k8s/overlays/aws-cdc/kafka-cluster.yaml`/
`mongodb-community.yaml` aren't Terraform-managed, so there's no CI/CD path
for this part regardless; it stays a local, manual `kubectl delete pvc` +
reapply with `size: 1Gi`, same as `AWS_PLAN.md`'s own scoping already says.

**Real state checked during the 2026-09-14 follow-up session, to build on
rather than rediscover:**

- **This repo has a real git remote**: public `events-api` under
  `viacheslavbinetskyiaws-ctrl` (a *different* GitHub account than
  commit-author email alone would suggest — verify live via `ssh -T
  git@github-aws-personal` if this ever needs re-confirming). `git
  log`/`git status` should both be clean going into this session.
- **Five OIDC-federated IAM roles exist** in `terraform/modules/github-oidc/`
  (`ecr_push`, `terraform_plan`, `terraform_apply`, `deploy` — plus
  whatever this milestone's own work adds) — no static AWS credentials
  anywhere in GitHub. `terraform_apply` holds `AdministratorAccess` and is
  the one that'll actually run this milestone's RDS/addon changes.
- **Every GitHub Actions trigger context gets its own distinct OIDC `sub`
  claim shape** — this bit Milestone 10 three separate times (`push`,
  `pull_request`, and a job referencing `environment:` are all genuinely
  different formats, confirmed against GitHub's own docs each time, never
  assumed from one to infer another). Worth remembering if this milestone's
  workflow usage ever hits a similar `AssumeRoleWithWebIdentity` denial —
  check the actual current docs for the exact trigger context in play,
  don't extrapolate from a different one that happened to work.
- **`aws_eks_access_entry.creator` (`modules/eks/main.tf`) and
  `k8s_viewer_trust` (`modules/iam/main.tf`) are hardcoded** to
  `terraform-events-api`'s IAM user ARN, not derived from
  `data.aws_caller_identity.current.arn` — that broke the moment Terraform
  started also running via an assumed role (CI). Local applies are
  unaffected (that hardcoded ARN is exactly the identity local applies
  already authenticate as via the `events-api-tf` profile). Worth
  remembering when adding the new `vpc-cni`/`kube-proxy` addons: if
  anything about their setup references the applying identity dynamically,
  check it the same way.
- RDS `allocated_storage` is still `50` (not yet shrunk — that's this
  milestone's actual job) — see the real-usage numbers already given above,
  no need to re-derive the verified minimums (`5GiB` RDS, `1Gi` EBS floor).
- **Real, calculated savings from the storage-shrink part: roughly
  $5-6/month** (gp2 ≈ $0.119/GB-mo, gp3 ≈ $0.095/GB-mo, both AWS Pricing
  API-verified) — small, not urgent on its own; the actual value here is
  exercising the new CI/CD pipeline for real, plus closing out a
  documented-but-deferred item. The addon-adoption part has **zero cost
  impact either way** — it's a scheduler-accounting fix, not a resource
  reduction (see `AWS_PLAN.md`'s own note on this).
- **This is a full teardown of Milestone 3/5's data plane, not an isolated
  change** — after the RDS instance is recreated, the full CDC verification
  needs redoing from scratch: both Postgres publications
  (`create-publications.sql`), both replication slots, both Debezium
  connectors, both BigQuery sink connectors, `alembic upgrade head` again,
  the one-time `GRANT rds_iam` bootstrap again. Same shape as the original
  storage-full incident's recovery (Milestone 5), which already rebuilt
  this once — that session's own notes are the closest precedent for what
  to expect.

**Start with `superpowers`'s `brainstorming` skill, not straight
implementation** — same precedent as every milestone since 5. Real design
surface here despite the mechanical-sounding scope: exactly how to sequence
the RDS Terraform change and the new addon adoption through PRs (one
combined PR or split), whether the Kafka/Mongo PVC resize happens before or
after the RDS change, how `resolve_conflicts_on_create` should be set for
adopting the already-running self-managed `vpc-cni`/`kube-proxy` (verify
current AWS docs for this rather than copy the `ebs_csi` addon's exact
setting without checking it still applies the same way), and how much of
the post-recreate CDC re-verification needs to happen before the milestone
can be called done versus deferred to a follow-up note.

Follow CLAUDE.md's hands-on teaching mode by default: explain what needs
to change and why, hand over the actual commands/edit content, let me
run/apply it myself, then verify afterward — same discipline used
throughout Milestone 10 and the 2026-09-14 follow-up, including reading
files back after every edit before trusting they match.

**Plugins to use this session:**
- `superpowers` — `brainstorming` → `writing-plans` before implementation
  (see above).
- `terraform` — the RDS module edit is small, but verify current
  `aws_db_instance` docs for `allocated_storage`/`storage_type` behavior on
  a destroy/recreate rather than assume it hasn't changed since Milestone 2's
  own `storage_encrypted` replacement already taught this project that RDS
  replacement doesn't always change what you'd expect (the endpoint hostname
  didn't change last time, for instance — don't assume this time is
  identical either, check). Also verify current `aws_eks_addon` docs for
  `vpc-cni`/`kube-proxy` specifically — exact addon names, current default
  versions for this cluster's Kubernetes version, and `configuration_values`
  schema for setting resource requests, rather than assume from the
  `ebs_csi` addon's own (different) configuration shape.
- `aws-core` — its `aws-database`/RDS-specific guidance for the actual
  destroy/recreate mechanics; its `aws-secrets-manager` skill's standing
  constraint still applies (the master password lives in Secrets Manager,
  never as plaintext).

Not relevant this milestone: `bigquery-data-analytics`, `mongodb` (beyond
the mechanical PVC resize — no schema/query work), `frontend-design`,
`claude-md-management`, `skill-creator`, `warp`, `code-simplifier`,
`playwright`/`claude-in-chrome` (checking a GitHub Actions run is more
naturally done via the `gh` CLI — not installed as of this writing — or the
web UI than a browser). `claude-security` isn't the obvious fit either —
this milestone doesn't add new attack surface the way Milestone 10 did
(new public repo, new federated trust relationship); it's a storage-sizing
and scheduler-accounting cleanup on infrastructure that already exists.
