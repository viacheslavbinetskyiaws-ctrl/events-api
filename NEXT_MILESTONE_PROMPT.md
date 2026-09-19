# Next Session: Fully CI/CD-Driven Bootstrap (new initiative — not yet in AWS_PLAN.md)

Start by reading `WHATS_NEXT.md`'s Milestone 11 and Milestone 12 entries in
full — both closed out in the same session that scoped this initiative, and
both surfaced real, specific findings this prompt leans on directly (the
RDS-storage-decrease AWS limitation, the dbt image bug, the capacity-crunch
audit, the WAL/storage investigation, the GCP import verification). Then
read `AWS_PLAN.md`'s own Context section and Milestone 12 entry for how this
project has scoped and sequenced every prior milestone — this new
initiative needs the same discipline, since it's genuinely new, unscoped
ground: it is **not yet a numbered milestone in `AWS_PLAN.md`**. The first
real job next session is to scope and design it properly (via
`superpowers`'s `brainstorming` → `writing-plans`, same as every milestone
since 5 — this one is architectural, not bounded, given it spans Terraform,
Kubernetes, database bootstrapping, and external service registration as
one coherent pipeline), likely adding it as a real numbered milestone to
`AWS_PLAN.md` once scoped, not just implementing ad hoc.

## The actual goal, in the user's own words from the session that scoped this

"I want CI/CD to update or rebuild when something changes. And if I stop
everything, I want CI/CD to recreate everything later" — with "no or
minimum manual local commands." Two distinct capabilities, both currently
missing:

1. **Incremental apply when something changes** — already mostly true for
   Terraform (the existing `plan`/`apply` pipeline from Milestone 10), not
   true at all for the Kubernetes/database/external-service side of this
   stack.
2. **Full recreate from a torn-down state** — this is the harder one, and
   the one this whole initiative is really about. Today, recovering this
   entire stack from zero (which is exactly what the session that scoped
   this initiative had to do, start to finish, after a real RDS
   destroy/recreate) took many hours of manual, sequential, hands-on
   investigation and one-off `kubectl run` pods. None of it is currently
   push-button, and several of the fixes discovered along the way were
   genuine one-time bugs (now fixed permanently in code) rather than
   necessary manual steps — but real manual sequencing remains.

## The real manual steps this session catalogued, in the order they're needed

Use this list as the actual scope inventory — it's grounded in what
genuinely had to happen by hand this session, not a guess:

1. **`terraform destroy -target=module.rds.aws_db_instance.this` then a
   normal `apply`** — needed because real AWS RDS genuinely cannot decrease
   `allocated_storage` in place (confirmed this session against current AWS
   docs, not assumed) and the CI/CD pipeline's `apply` job has no
   `-target` support. Two real design options surfaced but not chosen
   between: (a) build `-target` support into the pipeline's `apply`
   workflow (a new `workflow_dispatch` input, real but narrow scope), or
   (b) restructure the RDS resource to use `lifecycle { create_before_destroy
   = true }` with a forced-new attribute (like renaming `identifier`) so a
   single ordinary `apply` handles it — real trade-off: a new identifier
   likely means a new endpoint hostname (this project's own history shows
   the endpoint *didn't* change on a same-identifier replace, but that's a
   different case — verify fresh, don't assume, if this path is chosen).
   This decision needs to happen during next session's design pass, not be
   assumed here.
2. **Migration Job trigger** — already fully IRSA-based (no static
   credentials), but still needs `kubectl delete job` + `kubectl apply -k`
   run from somewhere. Genuinely close to CI-ready already; needs a scoped
   role/RBAC `Role` for just this Job (matching the established
   one-workload-one-identity convention — `migrate`/`dbt`/`app`/`deploy`
   each already have their own IRSA role and their own RBAC surface, never
   share one).
3. **The two `GRANT rds_iam` bootstraps** (`events`, then `events_app`) —
   currently one-off manual `kubectl run` pods. Real sequencing constraint
   confirmed live this session, twice: a genuinely fresh RDS instance's
   `events` role can *only* authenticate via the Secrets-Manager-held
   master password until the first `GRANT rds_iam TO events` succeeds
   (IAM auth for a role with no `rds_iam` grant yet fails with a plain
   `InvalidPasswordError`, not the PAM-specific error a role that *has* the
   grant produces) — so the very first bootstrap step on a fresh instance
   cannot use IAM auth at all, by construction. Automating this needs a Job
   with its own IRSA role that can *read* the RDS-managed Secrets Manager
   secret via a runtime dynamic reference (never through an LLM or a local
   shell — the project's own `aws-secrets-manager` skill covers this
   pattern) to perform that one bootstrap connection, then everything after
   can use IAM auth normally.
4. **Publications Job** (`aws-cdc-create-publications`) — **already fixed
   this session** to use the same IRSA pattern as the migration Job (no
   more static Secret) — this one is essentially CI-ready already; it just
   needs the same `kubectl delete job` + `apply -k` trigger as #2.
5. **Debezium connector registration and recovery** — the connector
   manifests themselves are already fully declarative
   (`k8s/overlays/aws-cdc/kafka-connectors.yaml`), but *recovery* from a
   stale/lost replication slot (drop the `KafkaConnector` CR to release the
   slot, `pg_drop_replication_slot`, reapply the CR to recreate fresh) is
   still a manual, investigative runbook today, exercised for real multiple
   times this session. Worth deciding whether this becomes a scripted
   Job/workflow step (the mechanics are now well-understood and repeatable)
   or stays a documented manual runbook for the rare case it's needed.
6. **The `debezium_replication` password rotation** — deliberately left
   manual this session, on purpose, after weighing the real trade-off:
   automating it means granting some ServiceAccount write access to
   Kubernetes Secrets, a genuinely more sensitive privilege class than
   anything else this project's automation currently touches (read-only
   SQL, or SQL scoped to a role's own tables). This is a real decision to
   make explicitly next session, not default into either direction.
7. **The Kafka/Mongo PVC resize dance** — plain `kubectl`, deliberately
   never going through Terraform (no Kubernetes/Helm provider in this
   project, by design). The two mechanics differ genuinely (Strimzi
   actively rejects an in-place decrease; a native `StatefulSet`'s
   `volumeClaimTemplates` is simply immutable) — both are now well-
   understood, repeatable procedures, but still hand-run today.

## The real architectural blocker already named, twice, in this project's own history

`k8s/overlays/aws` — which every CDC/realtime/dbt overlay chains through —
contains the RBAC `Role`/`RoleBinding` objects themselves
(`viewer-rbac.yaml`, `deploy-rbac.yaml`). Granting any automated CI identity
broader `kubectl apply -k` access without first pulling those RBAC objects
into their own overlay (one an automated identity never touches) is a real
privilege-escalation anti-pattern — confirmed against AWS's own
access-policy docs (`AmazonEKSEditPolicy` has zero coverage for third-party
CRDs like Strimzi's and the MongoDB Community Operator's anyway, a second,
independent reason the current narrow `deploy` scope can't just be widened).
**Closing this properly, if the design calls for broader `kubectl apply -k`
access from CI at all, means restructuring the manifests first** — not just
loosening an IAM policy. This may not even be necessary depending on how
next session's design lands (several narrow, purpose-specific Jobs following
the existing one-workload-one-identity convention may cover most of the
real need without ever widening `deploy`'s own scope).

## Real technical assets already in place to build on, not rediscover

- The migration Job and (as of this session) the publications Job both
  already demonstrate the working pattern for a CI-triggerable, credential-
  free (beyond IRSA) Job: `serviceAccountName` bound to a scoped IRSA role,
  an `initContainer` (or the main container itself) minting a fresh IAM
  token via `aws rds generate-db-auth-token`, no static Secret anywhere.
  Any new automated bootstrap step should copy this shape, not invent a new
  one.
- The Dockerfile's `dbt_packages` bug (every CI-built dbt image was
  silently broken) is fixed — future full-recreate cycles will build
  correctly without rediscovering this.
- All 9+ resource right-sizing fixes from this session, plus the 3 WAL
  parameter corrections, plus the GCP WIF Terraform module, are already
  committed as code (once this session's own work is committed — check
  `git status` first) — a fresh recreate from zero will already deploy with
  every one of these fixes baked in, with nothing extra needed there.

## Plugins to use this session

- `superpowers` — `brainstorming` → `writing-plans`, and treat this as
  architectural (new subsystem spanning multiple layers), not bounded —
  same reasoning this session used to classify Milestone 11/12 as bounded
  extensions of existing patterns, which this genuinely is not.
- `terraform` — for the RDS destroy-vs-create_before_destroy decision
  specifically; verify current `lifecycle` / `create_before_destroy`
  interaction with `identifier` against the real provider docs rather than
  assume, same discipline the GCP import work used this session.
- `aws-core` — `aws-secrets-manager` for the master-password-read design
  (runtime dynamic references, never a raw fetch) and `aws-iam` for
  scoping any new IRSA role/RBAC `Role` narrowly, matching the existing
  one-workload-one-identity convention exactly.
- `claude-security` — worth considering once a concrete design exists,
  specifically for the "does this new automated identity's privilege scope
  actually stay narrow" question (the RBAC-in-overlay blocker and the
  Secrets-write trade-off for password rotation are exactly its kind of
  review) — not needed for the brainstorming/design pass itself.

Not relevant this session: `bigquery-data-analytics`, `mongodb` (beyond
whatever the PVC-resize automation touches mechanically), `frontend-design`,
`code-simplifier`, `warp`, `playwright`/`claude-in-chrome`.
