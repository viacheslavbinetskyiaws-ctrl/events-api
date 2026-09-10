# Next Session: AWS_PLAN.md Milestone 10

Start AWS_PLAN.md Milestone 10 (CI/CD). Read `WHATS_NEXT.md` first for full
current state (the Milestone 9 entry has the real ALB Ingress story — a
genuine credential-architecture bug fix for Debezium, two rounds of real
memory-pressure debugging, and two independent end-to-end SSE proofs), then
`AWS_PLAN.md`'s Milestone 10 section for scope: GitHub Actions running the
existing `pytest` suite on push, building and pushing all four Docker images
(app, streaming, dbt, plus the Node `realtime` service) to ECR on merge to
main. Deployment automation itself gets documented as "how this extends to
auto-deploy," not fully built — this project's per-session teardown
discipline means there's usually no live cluster to auto-deploy onto.

Before starting, confirm what's actually still live on AWS the same way every
prior milestone has — EKS/RDS were both still `ACTIVE`/`available` at the end
of the Milestone 9 session (confirmed via `describe-cluster`/
`describe-db-instances`), and the **full CDC/streaming stack is up and
healthy** (not scaled to zero, unlike every previous milestone's starting
point) — but check again rather than trust that a session boundary didn't
change anything.

**Real state checked at the end of the Milestone 9 session, to build on
rather than rediscover:**

- **This repo has never been pushed anywhere** — `git remote -v` is empty.
  Milestone 10 is the first time this project gets a real git remote at all,
  not just a CI/CD add-on to an existing one.
- **20 files are uncommitted right now**, all real Milestone 9 work (new IRSA
  role + vendored IAM policy in `modules/iam/`, the `Ingress` + ALB controller
  ServiceAccount overlay, the `debezium_replication` role migration, Kafka/
  Mongo/ALB-controller resource right-sizing, two new `helm/*/values-override.yaml`
  files). **This needs to be committed before anything else in Milestone 10** —
  both as basic hygiene and because creating a GitHub repo and wiring Actions
  needs a real commit history to push, not a clean slate.
- **GitHub decided over GitLab** — deliberated explicitly this session, not
  arbitrary. The target job posting (`project_target_job_posting` memory)
  names no CI/CD platform at all, so this wasn't a stack-matching decision
  like MongoDB-over-DocumentDB was — it came down to portfolio value (a public
  GitHub repo is the more standard "here's my work" artifact to link) and
  freshness (GitHub Actions is the user's past experience, not their current
  day-job tool, so redoing it here adds more than restating already-current
  GitLab skills would). **Recommend a public repo specifically** — confirmed
  live via GitHub's own current billing docs that public repos get
  unconditionally free, unlimited-minute Actions usage, vs. 2,000 free
  minutes/month on a private repo under the Free plan. Worth confirming this
  is still what the user wants before creating the repo, not assuming.
- **`realtime/` has its own, separate `Dockerfile`** — grepped and confirmed:
  the root `Dockerfile` only has three build targets (`runtime`,
  `runtime-streaming`, `runtime-dbt`), all Python. The Node service builds
  from a completely different Docker context (`realtime/`), not a fourth
  target on the same Dockerfile. The GitHub Actions workflow needs two
  distinct build contexts, not one build matrix over four targets of the same
  file.
- **No AWS credentials mechanism for GitHub Actions exists yet.** This
  project has been consistently anti-static-credential everywhere else (RDS
  IAM auth instead of passwords, EKS IRSA instead of node-level AWS keys, GCP
  WIF instead of service-account key files) — the same instinct applies here:
  GitHub Actions supports OIDC federation directly to an AWS IAM role
  (`aws-actions/configure-aws-credentials`'s documented OIDC path), which
  needs a **new Terraform IAM role** in `modules/iam/` (a GitHub OIDC identity
  provider + a role trusting it, scoped by repo/branch via the token's `sub`
  claim) — the same shape as every other IRSA-style role in this file, just
  federated from GitHub's OIDC issuer instead of the EKS cluster's. Verify
  GitHub's current OIDC provider URL/thumbprint and the exact `sub` claim
  format against GitHub's own current docs at implementation time, not from
  training-data memory — not something to assume unchanged.
- **`ecr:GetAuthorizationToken`/`ecr:PutImage`-shaped permissions don't exist
  on any current IAM role** — the new GitHub Actions role needs its own
  policy scoped to the specific ECR repos this project already has in
  `modules/ecr/` (four repos: app, streaming, dbt, realtime — confirm the
  realtime one's exact name live rather than guess it).
- Full CDC pipeline verified twice, end-to-end, through the real ALB DNS
  name — both before and after the Milestone 9 capacity fixes. All 4 nodes
  sit at 70-85% real memory, none over capacity. `dbt-build` CronJob runs
  confirmed actually completing (not just scheduled) under the corrected
  resource requests.

**Start with `superpowers`'s `brainstorming` skill, not straight
implementation** — same precedent as Milestones 6-9. Real design surface
here: the exact GitHub OIDC trust-policy shape and its `sub`-claim scoping
(all branches vs. `main`-only vs. per-environment), the workflow's trigger
split (tests on every push vs. image build+push gated to merges to `main`
specifically), whether CI runs the existing integration tests (which need a
real Postgres) or just the unit suite, and the two-Docker-context build
matrix.

Follow CLAUDE.md's hands-on teaching mode by default: explain what needs
to change and why, hand over the actual commands/edit content, let me
run/apply it myself, then verify afterward.

**Plugins to use this session:**
- `superpowers` — `brainstorming` → `writing-plans` before implementation
  (see above).
- `terraform` — the new GitHub OIDC provider + IAM role + ECR-scoped policy
  in `modules/iam/`, following the existing IRSA-shaped pattern in that file.
- `aws-core` — its `aws-iam` skill covers the new OIDC trust-policy
  correctness (a real, easy-to-get-wrong surface — GitHub's own docs warn
  about exactly this); its `aws-secrets-manager` skill's hook still carries
  forward as a standing constraint even though this milestone doesn't touch
  RDS/Secrets Manager directly.
- `claude-security` — worth an explicit pass this time, not just an offer —
  this milestone creates a new public-facing GitHub repo *and* a new
  federated-identity trust relationship into this AWS account, a genuinely
  larger new attack surface than most prior milestones.

Not relevant this milestone: `bigquery-data-analytics`, `mongodb`,
`frontend-design`, `claude-md-management`, `skill-creator`, `warp`,
`code-simplifier`, `playwright`/`claude-in-chrome` (checking a GitHub Actions
run is more naturally done via the `gh` CLI than a browser). `commit-commands`
*is* relevant this time, unlike most prior milestones — this is the first
session where committing and pushing to a real remote is itself part of the
milestone's own scope, not just end-of-session housekeeping.
