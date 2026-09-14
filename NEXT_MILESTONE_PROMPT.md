# Next Session: AWS_PLAN.md Milestone 11

Start AWS_PLAN.md Milestone 11 (shrink storage to real minimums, deferred
from Milestone 5). Read `WHATS_NEXT.md` first for full current state (the
Milestone 10 entry has the real CI/CD story — nine real bugs found and
fixed getting GitHub Actions' OIDC federation actually working, most in the
trust-policy surface itself), then `AWS_PLAN.md`'s Milestone 11 section for
scope: RDS `allocated_storage` destroyed/recreated from `50`/`gp2`/
`max_allocated_storage=100` down to the real verified minimum (`5` GiB on
`db.t4g.micro`/postgres18/`gp2`), and the Kafka/Mongo EBS PVCs (currently
`5Gi`/`2Gi`+`1Gi`) shrunk to their real EBS floor (`1Gi` each) — both grow-only
by AWS/Kubernetes' own documentation, so this is destroy/recreate, not a
config edit.

Before starting, confirm what's actually still live on AWS the same way
every prior milestone has — EKS/RDS were both still `ACTIVE`/`available` as
of 2026-09-14 (confirmed via `describe-cluster`/`describe-db-instances`, RDS
storage still at the pre-shrink `50` GiB), all 34 pods across all 3
namespaces `Running`/`Completed`, nothing crash-looping — but check again
rather than trust that a session boundary didn't change anything.

**Real usage numbers, freshly re-verified 2026-09-14 (supersede the stale
Milestone-5-era figures an earlier draft of this file cited)** — all three
storage targets confirm the same story, real usage nowhere near even the
shrunk-to minimums: RDS **~3.5GiB used of 50GiB allocated** (~7%, via
CloudWatch `FreeStorageSpace`) against a verified `5GiB` real minimum;
Kafka PVC **92Mi used of 5Gi** (2%); Mongo data-volume **386Mi used of 2Gi**
(20%); Mongo logs-volume **69Mi used of 1Gi** (8%) — against a verified
`1Gi` EBS floor for all three. CPU is a non-issue cluster-wide (1-5% node
utilization; every pod checked uses 3-15% of its own CPU *request* size)
and — worth remembering rather than re-deriving — **reducing CPU requests
would not reduce the AWS bill at all**: this project runs fixed-size EC2
node groups, not Fargate, so AWS bills for the 4 provisioned `t4g.small`
instances regardless of what pods request; only changing `desired_size`/
instance type in `terraform/modules/eks/main.tf` would move that number,
and that's not realistically achievable right now regardless (see next).

**One new finding worth planning around**: node `ip-10-0-11-18` is
currently at **105% real memory** (pod-level requests sum to 93%, but real
usage — plus real kubelet/system overhead `kubectl top pods` doesn't
capture — pushes it over). Nothing is currently crash-looping or evicting,
but this node has zero real headroom. Since this milestone's Kafka/Mongo
PVC resize means deleting and recreating those pods (a real restart, not
just a config reload), check `kubectl top nodes` again fresh at the start
of the session rather than assume this figure — if it's still tight, expect
the same class of memory-pressure debugging Milestone 9 already went
through once, not a surprise.

**This is the first real exercise of the Terraform CI/CD pipeline Milestone
10 just built — that's the whole reason the user wanted this milestone
picked up next.** The RDS storage change is a normal Terraform diff
(`allocated_storage` edited in `terraform/modules/rds/main.tf`, no
`storage_type` override needed since `gp2` is already the default in use)
and should flow through the real pipeline exactly as designed: a PR
touching `terraform/**` → `plan` runs automatically, read its output for
real before merging → merge → manually trigger `apply` via
`workflow_dispatch` (Actions → Terraform → Run workflow — no `gh` CLI
installed as of the Milestone 10 session, use the web UI unless that's
changed) → confirm it actually applied against real AWS and shows up in
the `aws-infra` Environment's deployment history. Don't fall back to a
local `terraform apply` for this one specifically unless the pipeline
itself is genuinely broken — that would defeat the actual point of doing
this milestone now rather than later.

**The Kafka/Mongo PVC resize is separate, plain `kubectl` work, not
Terraform** — `k8s/overlays/aws-cdc/kafka-cluster.yaml`/
`mongodb-community.yaml` aren't Terraform-managed, so there's no CI/CD path
for this part regardless; it stays a local, manual `kubectl delete pvc` +
reapply with `size: 1Gi`, same as `AWS_PLAN.md`'s own scoping already says.

**Real state checked at the end of the Milestone 10 session, to build on
rather than rediscover:**

- **This repo now has a real git remote for the first time**: public
  `events-api` under `viacheslavbinetskyiaws-ctrl` (a *different* GitHub
  account than commit-author email alone would suggest — verify live via
  `ssh -T git@github-aws-personal` if this ever needs re-confirming, don't
  re-derive it from email). `git log`/`git status` should both be clean
  going into this session.
- **Four OIDC-federated IAM roles exist** in `terraform/modules/github-oidc/`
  (`ecr_push`, `terraform_plan`, `terraform_apply`, `deploy`) — no static
  AWS credentials anywhere in GitHub. `terraform_apply` holds
  `AdministratorAccess` and is the one that'll actually run this
  milestone's RDS destroy/recreate.
- **Every GitHub Actions trigger context gets its own distinct OIDC `sub`
  claim shape** — this bit Milestone 10 three separate times (`push`,
  `pull_request`, and a job referencing `environment:` are all genuinely
  different formats, confirmed against GitHub's own docs each time, never
  assumed from one to infer another). Worth remembering if this milestone's
  workflow usage ever hits a similar `AssumeRoleWithWebIdentity` denial —
  check the actual current docs for the exact trigger context in play,
  don't extrapolate from a different one that happened to work.
- **`aws_eks_access_entry.creator` (`modules/eks/main.tf`) and
  `k8s_viewer_trust` (`modules/iam/main.tf`) are now hardcoded** to
  `terraform-events-api`'s IAM user ARN, not derived from
  `data.aws_caller_identity.current.arn` — that broke the moment Terraform
  started also running via an assumed role (CI). Local applies are
  unaffected (that hardcoded ARN is exactly the identity local applies
  already authenticate as via the `events-api-tf` profile).
- RDS `allocated_storage` is still `50` (not yet shrunk — that's this
  milestone's actual job) — see the real-usage numbers already given above,
  no need to re-derive the verified minimums (`5GiB` RDS, `1Gi` EBS floor).
- **Real, calculated savings from this whole milestone: roughly $5-6/month**
  (gp2 ≈ $0.119/GB-mo, gp3 ≈ $0.095/GB-mo, both AWS Pricing API-verified) —
  small, not urgent on its own; the actual value here is exercising the new
  CI/CD pipeline for real, plus closing out a documented-but-deferred item.
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
the RDS Terraform change through a PR (one PR for just the storage change,
or bundled with anything else pending), whether the Kafka/Mongo PVC resize
happens before or after the RDS change, and how much of the post-recreate
CDC re-verification needs to happen before the milestone can be called done
versus deferred to a follow-up note.

Follow CLAUDE.md's hands-on teaching mode by default: explain what needs
to change and why, hand over the actual commands/edit content, let me
run/apply it myself, then verify afterward — same discipline used
throughout Milestone 10, including reading files back after every edit
before trusting they match.

**Plugins to use this session:**
- `superpowers` — `brainstorming` → `writing-plans` before implementation
  (see above).
- `terraform` — the RDS module edit itself is small, but verify current
  `aws_db_instance` docs for `allocated_storage`/`storage_type` behavior on
  a destroy/recreate rather than assume it hasn't changed since Milestone 2's
  own `storage_encrypted` replacement already taught this project that RDS
  replacement doesn't always change what you'd expect (the endpoint hostname
  didn't change last time, for instance — don't assume this time is
  identical either, check).
- `aws-core` — its `aws-database`/RDS-specific guidance for the actual
  destroy/recreate mechanics; its `aws-secrets-manager` skill's standing
  constraint still applies (the master password lives in Secrets Manager,
  never as plaintext).

Not relevant this milestone: `bigquery-data-analytics`, `mongodb` (beyond
the mechanical PVC resize — no schema/query work), `frontend-design`,
`claude-md-management`, `skill-creator`, `warp`, `code-simplifier`,
`playwright`/`claude-in-chrome` (checking a GitHub Actions run is more
naturally done via the `gh` CLI — not installed as of Milestone 10 — or the
web UI than a browser). `claude-security` isn't the obvious fit either —
this milestone doesn't add new attack surface the way Milestone 10 did
(new public repo, new federated trust relationship); it's a storage-sizing
cleanup on infrastructure that already exists.
