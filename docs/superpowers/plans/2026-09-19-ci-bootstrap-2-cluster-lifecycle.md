# CI-Driven Bootstrap, Plan 2 of 2: Cluster Lifecycle (`cluster-up` / `cluster-down`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This repo is a learning project: default to teaching mode** (`CLAUDE.md`). The person running this plan types the commands and makes the edits; an assistant hands over the exact content, explains why, then reads files/output back to check. Do not run mutating commands or write project files on their behalf unless they say so for that stretch of work. Do not `git commit` unless told to in that turn: every "Checkpoint" step below is the user's to run.

**Goal:** One `workflow_dispatch` recreates the entire stack from nothing (Terraform, Helm operators, manifests, database bootstrap, credentials, connectors, seed data, verification) and another tears it all down, with no local commands.

**Architecture:** Every infrastructure-specific value in the manifests is a sentinel that a shared Kustomize Component overwrites from a generated `cluster-config` ConfigMap. The database bootstrap is a chain of suspended Jobs that a script releases in order. Both workflows are thin: `infra` job (Terraform, `terraform_apply` role) and `platform` job (Kubernetes, `bootstrap` role) call scripts under `scripts/cluster/`.

**Tech Stack:** Kustomize (Components + `replacements`, verified on kubectl v1.36.1 / Kustomize v5.8.1), Helm, bash, Python 3.14 (`boto3`, `asyncpg` from the app image), GitHub Actions, Kubernetes Jobs (`spec.suspend`).

**Spec:** `docs/superpowers/specs/2026-09-19-ci-driven-bootstrap-design.md` (D5, D6, D7, Architecture, Verification). **Prerequisite:** Plan 1 (`2026-09-19-ci-bootstrap-1-terraform-foundations.md`) is complete through its Task 8: the `cluster/` root exists and has been applied by CI, `events-api-github-bootstrap` exists with its EKS cluster-admin access entry, and `events-api-kafka-connect` is built by CI.

## Global Constraints

- Region `eu-central-1`; account `938500344309`; real AWS CLI calls use `--profile events-api-tf` locally. In CI, credentials come only from OIDC.
- `bootstrap` (`events-api-github-bootstrap`) trusts `ref:refs/heads/main` only: the `platform` jobs must **not** declare an `environment:`. The `infra` jobs use `environment: aws-infra` with `terraform_apply`.
- ServiceAccounts / IRSA roles: `events-api-bootstrap-master` -> `events-api-iam-bootstrap-master-irsa`; `events-api-bootstrap-roles` -> `events-api-iam-bootstrap-roles-irsa`. Names must match Plan 1 exactly.
- Debezium secret `events-api/debezium-replication`; Kubernetes Secrets `debezium-db-credentials` (key `password`), `mongo-consumer-seed-password` (key `password`), `streaming-mongo-credentials` (key `STREAMING_MONGO_URI`).
- Secret values never appear in logs, `set -x` output, workflow outputs or the repo. Values are piped into `kubectl`, never assigned to a variable that could be echoed.
- The bootstrap-chain Jobs (`events-api-bootstrap-master`, `events-api-migrate`, `events-api-bootstrap-roles`, `aws-cdc-create-publications`) are deleted and re-applied (suspended) on every `up` and released in order. The seed Job is neither deleted nor re-applied once it has **succeeded**; a failed one is deleted and recreated, so a failure can never wedge later runs.
- Chart pins (verified against the live cluster on 2026-09-19, user values identical to the committed files): `metrics-server` 3.14.0 (`kube-system`), `aws-load-balancer-controller` 3.5.0 (`kube-system`), `strimzi-kafka-operator` 1.2.0 (`events-api`), `community-operator` 0.13.0 (`events-api`), `kube-prometheus-stack` 90.0.0 (`monitoring`).
- Shell is zsh locally: brace `${VAR}` before a colon. Scripts use `#!/usr/bin/env bash`.
- Do not state an expected count/outcome without having read the real output; acceptance criteria are conditions to check.

## File Structure

| File | Responsibility |
|---|---|
| `k8s/components/cluster-config-source/kustomization.yaml` (new) | Generates the `cluster-config` ConfigMap from a gitignored env file |
| `k8s/components/cluster-config-replacements/kustomization.yaml` (new) | Overwrites every infra-specific manifest value from that ConfigMap |
| `scripts/cluster/check-render.sh`, `sample-cluster-config.env` (new) | Renders overlays with sample values and fails if a real/placeholder value survives |
| `k8s/overlays/aws*/...` (modify) | Sentinels instead of real values; components wired in |
| `k8s/overlays/aws-connect/` (new; files moved from `aws-cdc`) | Kafka Connect, connectors, WIF config, cdc-consumer: applied after credentials exist |
| `k8s/overlays/aws-bootstrap/` (new) | Suspended Jobs + Python bootstrap scripts + publications SQL |
| `k8s/overlays/aws-seed/` (new) | Seed Job + `seed.py` |
| `scripts/cluster/{lib,render-config,platform-up,platform-down}.sh` (new) | The Kubernetes half of `up` and `down` |
| `.github/workflows/{cluster-up,cluster-down}.yaml` (new) | The two dispatch workflows |
| `tests/unit/test_bootstrap_roles.py`, `tests/unit/test_seed.py` (new) | Unit tests for the Python scripts' logic |

---

### Task 1: Dynamic-config layer with a render check (manifest TDD)

**Files:**
- Create: `scripts/cluster/check-render.sh`, `scripts/cluster/sample-cluster-config.env`, `k8s/components/cluster-config-source/kustomization.yaml`, `k8s/components/cluster-config-replacements/kustomization.yaml`
- Modify: `.gitignore`; `k8s/overlays/aws/{kustomization,service-account,migration-service-account,deployment-patch,migration-job-patch,app-configmap-patch}.yaml`; `k8s/overlays/aws-dbt/{kustomization,service-account,dbt-cronjob}.yaml`; `k8s/overlays/aws-realtime/{kustomization,deployment}.yaml`; `k8s/overlays/aws-alb-controller/{kustomization,service-account}.yaml`

**Interfaces:**
- Produces: a generated `k8s/components/cluster-config-source/cluster-config.env` (gitignored) with keys `ACCOUNT_ID`, `ECR_REGISTRY`, `RDS_HOST`, `AWS_REGION`, `RDS_MASTER_SECRET_ARN`, yielding ConfigMap `cluster-config` (namespace `events-api`, **no name hash**); the sentinels `000000000000` (account), `registry.placeholder.invalid` (registry), `rds.placeholder.invalid` (DB host), `placeholder-region`.
- Verified 2026-09-19 in scratch experiments: replacements accept a generator-produced ConfigMap as source; a shared Component works when included at several levels of a chain; `annotationSelector` existence matching, bracketed dotted keys (`spec.config.[database.hostname]`), env-list selectors and whole-value replacement all work; selectors that match nothing are harmless.

- [ ] **Step 1: Write the check first**

`scripts/cluster/sample-cluster-config.env`:
```
ACCOUNT_ID=111122223333
ECR_REGISTRY=111122223333.dkr.ecr.eu-west-1.amazonaws.com
RDS_HOST=sample-db.example123.eu-west-1.rds.amazonaws.com
AWS_REGION=eu-west-1
RDS_MASTER_SECRET_ARN=arn:aws:secretsmanager:eu-west-1:111122223333:secret:rds!db-sample
```

`scripts/cluster/check-render.sh`:
```bash
#!/usr/bin/env bash
# Renders Kustomize overlays with SAMPLE cluster values (an account and
# region that are not this project's) and fails if any real or placeholder
# infrastructure value survives. A survivor means a manifest site is not
# covered by k8s/components/cluster-config-replacements, which would silently
# point that workload at stale or invalid infrastructure.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ $# -eq 0 ]]; then
  echo "usage: $0 <overlay-name> [<overlay-name> ...]   (names under k8s/overlays/)" >&2
  exit 2
fi

# Work on a copy so a real generated cluster-config.env is never clobbered.
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
cp -R "${root}/k8s" "${work}/k8s"
if [[ -d "${work}/k8s/components/cluster-config-source" ]]; then
  cp "${root}/scripts/cluster/sample-cluster-config.env" \
    "${work}/k8s/components/cluster-config-source/cluster-config.env"
fi

status=0
for overlay in "$@"; do
  if ! rendered="$(kubectl kustomize "${work}/k8s/overlays/${overlay}" 2>&1)"; then
    echo "FAIL ${overlay}: kustomize build error"
    echo "${rendered}"
    status=1
    continue
  fi
  bad=0
  for pattern in '938500344309' 'choe4u6ye3yf' 'eu-central-1' '000000000000' 'placeholder\.invalid' 'placeholder-region'; do
    if grep -nE -- "${pattern}" <<<"${rendered}"; then
      echo "FAIL ${overlay}: '${pattern}' survived rendering"
      bad=1
    fi
  done
  if [[ ${bad} -eq 0 ]]; then
    echo "ok   ${overlay}"
  else
    status=1
  fi
done
exit ${status}
```
```bash
chmod +x scripts/cluster/check-render.sh
```

- [ ] **Step 2: Run it and watch it fail**

```bash
scripts/cluster/check-render.sh aws-dbt aws-alb-controller aws-observability
```
Expected: `FAIL` lines showing `938500344309` (and `choe4u6ye3yf`/`eu-central-1` for `aws` and `aws-dbt`), because the manifests still hold real values. (Earlier grep: 11 account-ID and 6 RDS-host occurrences across the tree.)

- [ ] **Step 3: Create the two components**

`k8s/components/cluster-config-source/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component

# The env file is generated by scripts/cluster/render-config.sh from Terraform
# outputs and is gitignored: nothing infrastructure-specific is committed.
# Without it, `kubectl kustomize`/`apply -k` fails to build, which is the safe
# failure (the alternative is silently deploying stale values).
#
# No name hash: the Jobs read this ConfigMap by its fixed name via envFrom,
# and the replacements below name it as their source.
configMapGenerator:
  - name: cluster-config
    namespace: events-api
    envs:
      - cluster-config.env
    options:
      disableNameSuffixHash: true
```

`k8s/components/cluster-config-replacements/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component

# Overwrites every infrastructure-specific value in the manifests with the
# real one from the generated `cluster-config` ConfigMap. The literals left in
# the manifests are deliberately invalid sentinels (000000000000,
# registry.placeholder.invalid, rds.placeholder.invalid, placeholder-region),
# so an unreplaced site fails loudly instead of quietly pointing at old
# infrastructure. scripts/cluster/check-render.sh enforces that none survive.
#
# Selectors that match nothing in a given overlay are harmless, so this one
# Component is included by every overlay that owns any target.
replacements:
  # IAM role ARNs: replace the account-ID segment of arn:aws:iam::<account>:role/...
  - source:
      kind: ConfigMap
      name: cluster-config
      fieldPath: data.ACCOUNT_ID
    targets:
      - select:
          kind: ServiceAccount
          annotationSelector: eks.amazonaws.com/role-arn
        fieldPaths:
          - metadata.annotations.[eks.amazonaws.com/role-arn]
        options:
          delimiter: ":"
          index: 4
      - select:
          kind: KafkaConnect
          name: events-connect
        fieldPaths:
          - spec.template.serviceAccount.metadata.annotations.[eks.amazonaws.com/role-arn]
        options:
          delimiter: ":"
          index: 4

  # Image registry host: the segment before the first "/" of every ECR image.
  - source:
      kind: ConfigMap
      name: cluster-config
      fieldPath: data.ECR_REGISTRY
    targets:
      - select:
          kind: Deployment
          name: events-api
        fieldPaths:
          - spec.template.spec.containers.[name=events-api].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: Job
          name: events-api-migrate
        fieldPaths:
          - spec.template.spec.containers.[name=migrate].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: Deployment
          name: realtime
        fieldPaths:
          - spec.template.spec.containers.[name=realtime].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: Deployment
          name: cdc-consumer
        fieldPaths:
          - spec.template.spec.containers.[name=cdc-consumer].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: CronJob
          name: dbt-build
        fieldPaths:
          - spec.jobTemplate.spec.template.spec.containers.[name=dbt-build].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: KafkaConnect
          name: events-connect
        fieldPaths:
          - spec.image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: Job
          name: events-api-bootstrap-master
        fieldPaths:
          - spec.template.spec.containers.[name=bootstrap-master].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: Job
          name: events-api-bootstrap-roles
        fieldPaths:
          - spec.template.spec.containers.[name=bootstrap-roles].image
        options:
          delimiter: "/"
          index: 0
      - select:
          kind: Job
          name: events-api-seed
        fieldPaths:
          - spec.template.spec.containers.[name=seed].image
        options:
          delimiter: "/"
          index: 0

  # Database host, whole value.
  - source:
      kind: ConfigMap
      name: cluster-config
      fieldPath: data.RDS_HOST
    targets:
      - select:
          kind: ConfigMap
          name: events-api-config
        fieldPaths:
          - data.APP_DB_HOST
      - select:
          kind: CronJob
          name: dbt-build
        fieldPaths:
          - spec.jobTemplate.spec.template.spec.containers.[name=dbt-build].env.[name=DBT_HOST].value
      - select:
          kind: KafkaConnector
          name: tenant-accounts-connector
        fieldPaths:
          - spec.config.[database.hostname]
      - select:
          kind: KafkaConnector
          name: events-connector
        fieldPaths:
          - spec.config.[database.hostname]

  # Region, whole value.
  - source:
      kind: ConfigMap
      name: cluster-config
      fieldPath: data.AWS_REGION
    targets:
      - select:
          kind: ConfigMap
          name: events-api-config
        fieldPaths:
          - data.APP_AWS_REGION
      - select:
          kind: CronJob
          name: dbt-build
        fieldPaths:
          - spec.jobTemplate.spec.template.spec.containers.[name=dbt-build].env.[name=APP_AWS_REGION].value
```

- [ ] **Step 4: Gitignore the generated file**

Append to `.gitignore`:
```
k8s/components/cluster-config-source/cluster-config.env
```

- [ ] **Step 5: Wire the components into the LEAF overlays**

**Rule (found the hard way while executing this task):** include both components in every **leaf** overlay, the ones that are actually applied, and **never in an intermediate overlay such as `aws` that patches resources.** A component's replacements run *before* the including overlay's own `patches`. Put them in `aws` and the replacement targeting `data.APP_DB_HOST` runs before `app-configmap-patch.yaml` has added that key, and the build fails with `unable to find field "data.APP_DB_HOST" in replacement target` (the base ConfigMap has no such key). Verified with a scratch experiment: components in the leaf `aws-dbt` build fine, and every value lands. Consequences:
- `aws` and `aws-cdc` are **intermediate**: never apply them directly, and do not list them in `check-render.sh`.
- Every leaf lists **both** components: `aws-dbt`, `aws-realtime`, `aws-observability`, `aws-alb-controller` here, and the new standalone overlays later.

`k8s/overlays/aws/kustomization.yaml`: **no components.** Add this header comment above `resources:` so nobody applies it directly:
```yaml
# INTERMEDIATE overlay: never apply this one directly. It still holds the
# invalid sentinels for the account ID, registry, RDS host and region. The
# cluster-config components live in the LEAF overlays that chain through this
# one (aws-dbt, aws-realtime, aws-observability, ...), because a component's
# replacements run before the including overlay's own patches: put them here
# and they would run before app-configmap-patch.yaml has added the very keys
# they need to overwrite.
```
`k8s/overlays/aws-dbt/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - service-account.yaml
  - dbt-cronjob.yaml

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```
`k8s/overlays/aws-realtime/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws-cdc
  - deployment.yaml
  - service.yaml

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```
`k8s/overlays/aws-observability/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - service-monitor.yaml
  - grafana-dashboard-configmap.yaml

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```
`k8s/overlays/aws-alb-controller/kustomization.yaml` (standalone: needs both):
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - service-account.yaml

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```

- [ ] **Step 6: Replace the real values with sentinels**

`k8s/overlays/aws/service-account.yaml`:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: events-api-app
  namespace: events-api
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::000000000000:role/events-api-iam-app-irsa
```
`k8s/overlays/aws/migration-service-account.yaml`:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: events-api-migrate
  namespace: events-api
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::000000000000:role/events-api-iam-migration-irsa
```
`k8s/overlays/aws/deployment-patch.yaml`: change only the image line to `image: registry.placeholder.invalid/events-api-app:latest`.

`k8s/overlays/aws/migration-job-patch.yaml` (sentinel image, and **suspended**: the bootstrap script releases it only after Job 1 has granted `rds_iam`):
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: events-api-migrate
  namespace: events-api
spec:
  suspend: true
  template:
    spec:
      serviceAccountName: events-api-migrate
      containers:
        - name: migrate
          image: registry.placeholder.invalid/events-api-app:latest
          imagePullPolicy: Always
          envFrom:
            - configMapRef:
                name: events-api-config
```
`k8s/overlays/aws/app-configmap-patch.yaml`: change `APP_DB_HOST` to `rds.placeholder.invalid` and `APP_AWS_REGION` to `placeholder-region` (leave the other keys).

`k8s/overlays/aws-dbt/service-account.yaml`: role ARN becomes `arn:aws:iam::000000000000:role/events-api-iam-dbt-irsa`.

`k8s/overlays/aws-dbt/dbt-cronjob.yaml`: image becomes `registry.placeholder.invalid/events-api-dbt:latest`; in `env`, `APP_AWS_REGION` value becomes `placeholder-region` and `DBT_HOST` value becomes `rds.placeholder.invalid`.

`k8s/overlays/aws-realtime/deployment.yaml`: image becomes `registry.placeholder.invalid/events-api-realtime:latest`.

`k8s/overlays/aws-alb-controller/service-account.yaml`: role ARN becomes `arn:aws:iam::000000000000:role/events-api-iam-alb-controller-irsa`.

(`aws-cdc` files are edited in Task 2.)

- [ ] **Step 7: Run the check and watch it pass**

```bash
scripts/cluster/check-render.sh aws-dbt aws-alb-controller aws-observability
```
Acceptance: three `ok` lines (`aws-dbt`, `aws-alb-controller`, `aws-observability`), exit code 0. Then confirm the substitution really happened (not merely that nothing bad survived):
```bash
mkdir -p /tmp/kcheck && cp -R k8s /tmp/kcheck/ && cp scripts/cluster/sample-cluster-config.env /tmp/kcheck/k8s/components/cluster-config-source/cluster-config.env
kubectl kustomize /tmp/kcheck/k8s/overlays/aws-dbt | grep -nE 'role-arn|image:|APP_DB_HOST|APP_AWS_REGION|DBT_HOST' ; rm -rf /tmp/kcheck
```
Acceptance: every hit shows the sample values (`111122223333`, `eu-west-1`, `sample-db...`). `aws-realtime` and `aws-cdc` are not checked yet (Task 2). If a legitimate string trips the `eu-central-1` pattern in another overlay, remove that string from the manifest rather than weakening the check.

- [ ] **Step 8: Checkpoint (user runs)**

```bash
git add scripts/cluster k8s .gitignore
git status --short
```
Commit on a new branch `ci-bootstrap-cluster-lifecycle` (message: `Make infra-specific manifest values dynamic via a shared Kustomize Component`). Later tasks land in the same PR.

---

### Task 2: Split `aws-cdc`: an `aws-connect` overlay for what starts after credentials

**Why:** Kafka Connect, the connectors and `cdc-consumer` reference Secrets (`debezium-db-credentials`, `streaming-mongo-credentials`) that only exist after the bootstrap Jobs and the Mongo operator have run. Applying them in the same `kubectl apply` as Kafka/Mongo would start them too early.

**Files:**
- Create: `k8s/overlays/aws-connect/kustomization.yaml`
- Move (`git mv`): `aws-cdc/{kafka-connect,kafka-connectors,bigquery-connectors,gcp-wif-credential-config,consumer-deployment}.yaml` -> `aws-connect/`; `aws-cdc/create-publications-job.yaml` and `aws-cdc/scripts/create-publications.sql` -> `aws-bootstrap/` (edited in Task 3)
- Modify: `k8s/overlays/aws-cdc/kustomization.yaml`, and three moved files (sentinels)

- [ ] **Step 1: Move the files**

```bash
mkdir -p k8s/overlays/aws-connect k8s/overlays/aws-bootstrap/scripts
for f in kafka-connect kafka-connectors bigquery-connectors gcp-wif-credential-config consumer-deployment; do
  git mv k8s/overlays/aws-cdc/${f}.yaml k8s/overlays/aws-connect/${f}.yaml
done
git mv k8s/overlays/aws-cdc/create-publications-job.yaml k8s/overlays/aws-bootstrap/create-publications-job.yaml
git mv k8s/overlays/aws-cdc/scripts/create-publications.sql k8s/overlays/aws-bootstrap/scripts/create-publications.sql
ls k8s/overlays/aws-cdc
```
Acceptance: `aws-cdc` now holds only `kafka-cluster.yaml`, `kustomization.yaml`, `mongodb-community.yaml`, `storage-class.yaml` (and an empty `scripts/` directory you can `rmdir`).

- [ ] **Step 2: Rewrite the two kustomizations**

`k8s/overlays/aws-cdc/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - storage-class.yaml
  - kafka-cluster.yaml
  - mongodb-community.yaml
```
`k8s/overlays/aws-connect/kustomization.yaml` (standalone: it needs only the namespace, the `events-api-app` ServiceAccount and `events-api-config` ConfigMap, which `up` has already applied):
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - gcp-wif-credential-config.yaml
  - kafka-connect.yaml
  - kafka-connectors.yaml
  - bigquery-connectors.yaml
  - consumer-deployment.yaml

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```

- [ ] **Step 2b: Sentinels in the moved files**

- `aws-connect/kafka-connect.yaml`: the `image:` line becomes `image: registry.placeholder.invalid/events-api-kafka-connect:latest`; the `eks.amazonaws.com/role-arn` annotation becomes `arn:aws:iam::000000000000:role/events-api-iam-kafka-connect-gcp-irsa`.
- `aws-connect/kafka-connectors.yaml`: both `database.hostname:` values (in `tenant-accounts-connector` and `events-connector`) become `rds.placeholder.invalid`.
- `aws-connect/consumer-deployment.yaml`: the image becomes `registry.placeholder.invalid/events-api-streaming:latest`.

- [ ] **Step 3: Check**

```bash
scripts/cluster/check-render.sh aws-connect aws-dbt aws-realtime aws-observability aws-alb-controller
```
Acceptance: five `ok` lines (leaves only: `aws` and `aws-cdc` are intermediate). (`aws-bootstrap` has no kustomization yet and is not checked.)

- [ ] **Step 4: Checkpoint (user runs)**

`git add -A k8s && git status --short`; commit `Split aws-cdc: aws-connect for post-credential workloads`.

---

### Task 3: The `aws-bootstrap` overlay: suspended Jobs and their Python

**Files:**
- Create: `k8s/overlays/aws-bootstrap/{kustomization,service-accounts,job-bootstrap-master,job-bootstrap-roles}.yaml`, `k8s/overlays/aws-bootstrap/scripts/{bootstrap_master,bootstrap_roles}.py`, `tests/unit/test_bootstrap_roles.py`
- Modify: `k8s/overlays/aws-bootstrap/create-publications-job.yaml` (moved in Task 2)

**Interfaces:**
- Produces Jobs (all `spec.suspend: true`): `events-api-bootstrap-master`, `events-api-bootstrap-roles`, `aws-cdc-create-publications`. Consumed by Task 5's script, which releases them in order after `events-api-migrate`.
- `bootstrap_roles.py` exposes `canonical_debezium_password(secrets_client) -> str` (tested below) and reads env `RDS_HOST`, `AWS_REGION` (from `cluster-config`).

- [ ] **Step 1: Write the failing unit test**

`tests/unit/test_bootstrap_roles.py`:
```python
"""Unit tests for the Debezium-credential logic in the bootstrap-roles Job.
The script is not a package module (it lives beside its Job manifest), so it
is loaded by path. Only canonical_debezium_password is tested: the rest is
thin I/O against RDS/Secrets Manager that only a real cluster can exercise.
"""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "k8s/overlays/aws-bootstrap/scripts/bootstrap_roles.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("bootstrap_roles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSecrets:
    def __init__(self, stored: str | None):
        self.stored = stored
        self.put_calls: list[str] = []

    def describe_secret(self, SecretId):  # noqa: N803 - boto3 naming
        return {"VersionIdsToStages": {"v1": ["AWSCURRENT"]}} if self.stored else {}

    def get_random_password(self, **kwargs):
        assert kwargs["ExcludePunctuation"] is True
        return {"RandomPassword": "generated-password"}

    def put_secret_value(self, SecretId, SecretString):  # noqa: N803
        self.put_calls.append(SecretString)
        self.stored = SecretString

    def get_secret_value(self, SecretId):  # noqa: N803
        return {"SecretString": self.stored}


def test_generates_and_stores_a_password_on_the_first_run():
    module = load_script()
    secrets = FakeSecrets(stored=None)

    assert module.canonical_debezium_password(secrets) == "generated-password"
    assert secrets.put_calls == ["generated-password"]


def test_reuses_the_stored_password_without_writing_on_later_runs():
    module = load_script()
    secrets = FakeSecrets(stored="already-there")

    assert module.canonical_debezium_password(secrets) == "already-there"
    assert secrets.put_calls == []
```

- [ ] **Step 2: Run it and watch it fail**

```bash
uv run pytest tests/unit/test_bootstrap_roles.py -q
```
Expected: errors because `bootstrap_roles.py` does not exist yet (`FileNotFoundError` from `spec_from_file_location`/`exec_module`).

- [ ] **Step 3: Write the two scripts**

`k8s/overlays/aws-bootstrap/scripts/bootstrap_master.py`:
```python
"""Bootstrap Job 1: the one step that cannot use IAM auth.

A freshly created RDS instance's owner role (`events`) can only authenticate
with the RDS-managed master password until `GRANT rds_iam TO events` has run
(observed live: IAM auth for a role with no rds_iam grant fails with a plain
password-auth error). This script reads that password from Secrets Manager
(IRSA-scoped to that one secret), connects with it once, grants rds_iam and
exits. Every later Job authenticates over IAM. The password lives only in this
process's memory: never in a file, a log line or a Kubernetes Secret.

Re-running is safe: granting a role membership that already exists is a
NOTICE in Postgres, not an error.
"""

import asyncio
import json
import os

import asyncpg
import boto3


async def main() -> None:
    host = os.environ["RDS_HOST"]
    region = os.environ["AWS_REGION"]
    secret_arn = os.environ["RDS_MASTER_SECRET_ARN"]

    secrets = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(secrets.get_secret_value(SecretId=secret_arn)["SecretString"])

    conn = await asyncpg.connect(
        host=host,
        port=5432,
        user=secret["username"],
        password=secret["password"],
        database="events",
        ssl="require",
    )
    try:
        await conn.execute("GRANT rds_iam TO events")
        print("granted rds_iam to events")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

`k8s/overlays/aws-bootstrap/scripts/bootstrap_roles.py`:
```python
"""Bootstrap Job 2: post-migration role setup, over IAM as the owner role.

1. GRANT rds_iam TO events_app (the migration created the role; the API pods
   authenticate as it over IAM).
2. Make the debezium_replication role's password match the canonical value in
   Secrets Manager, generating and storing one on the very first run.
   Debezium's replication connection cannot use IAM auth (AWS: "you can't use
   IAM authentication to establish a replication connection"), so this role
   must stay password-authenticated. The value is never printed.

Both statements are safe to repeat on every `up`.
"""

import asyncio
import os

import asyncpg
import boto3

DEBEZIUM_SECRET_ID = "events-api/debezium-replication"
DEBEZIUM_ROLE = "debezium_replication"


def canonical_debezium_password(secrets) -> str:
    """Return the stored password, generating and storing one if the secret
    container is still empty (VersionIdsToStages is absent until the first
    PutSecretValue)."""
    described = secrets.describe_secret(SecretId=DEBEZIUM_SECRET_ID)
    if not described.get("VersionIdsToStages"):
        generated = secrets.get_random_password(
            PasswordLength=32, ExcludePunctuation=True
        )["RandomPassword"]
        secrets.put_secret_value(SecretId=DEBEZIUM_SECRET_ID, SecretString=generated)
        print("stored a newly generated Debezium password")
        return generated
    return secrets.get_secret_value(SecretId=DEBEZIUM_SECRET_ID)["SecretString"]


async def main() -> None:
    host = os.environ["RDS_HOST"]
    region = os.environ["AWS_REGION"]

    rds = boto3.client("rds", region_name=region)
    secrets = boto3.client("secretsmanager", region_name=region)

    token = rds.generate_db_auth_token(
        DBHostname=host, Port=5432, DBUsername="events", Region=region
    )
    password = canonical_debezium_password(secrets)

    conn = await asyncpg.connect(
        host=host,
        port=5432,
        user="events",
        password=token,
        database="events",
        ssl="require",
    )
    try:
        await conn.execute("GRANT rds_iam TO events_app")
        # Let the server quote the identifier and literal: no string
        # interpolation of the password into SQL on our side.
        alter = await conn.fetchval(
            "SELECT format('ALTER ROLE %I WITH PASSWORD %L', $1::text, $2::text)",
            DEBEZIUM_ROLE,
            password,
        )
        await conn.execute(alter)
        print("granted rds_iam to events_app; synced the debezium_replication password")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the tests and watch them pass**

```bash
uv run pytest tests/unit/test_bootstrap_roles.py -q
uv run ruff check k8s/overlays/aws-bootstrap/scripts tests/unit/test_bootstrap_roles.py
uv run ruff format --check k8s/overlays/aws-bootstrap/scripts tests/unit/test_bootstrap_roles.py
```
Acceptance: `2 passed`; ruff clean (run `uv run ruff format` on the files if the format check complains, then re-run).

- [ ] **Step 5: ServiceAccounts and the two Python Jobs**

`k8s/overlays/aws-bootstrap/service-accounts.yaml`:
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: events-api-bootstrap-master
  namespace: events-api
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::000000000000:role/events-api-iam-bootstrap-master-irsa
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: events-api-bootstrap-roles
  namespace: events-api
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::000000000000:role/events-api-iam-bootstrap-roles-irsa
```

`k8s/overlays/aws-bootstrap/job-bootstrap-master.yaml`:
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: events-api-bootstrap-master
  namespace: events-api
spec:
  # Applied suspended and released by scripts/cluster/platform-up.sh in
  # dependency order (this -> migration -> bootstrap-roles -> publications).
  suspend: true
  backoffLimit: 4
  activeDeadlineSeconds: 900
  template:
    spec:
      serviceAccountName: events-api-bootstrap-master
      restartPolicy: Never
      containers:
        - name: bootstrap-master
          image: registry.placeholder.invalid/events-api-app:latest
          imagePullPolicy: Always
          command: ["python", "/scripts/bootstrap_master.py"]
          envFrom:
            - configMapRef:
                name: cluster-config
          volumeMounts:
            - name: scripts
              mountPath: /scripts
              readOnly: true
          # Sized by estimate (boto3 + asyncpg, short-lived); check
          # `kubectl top` / OOMKilled on the first real run and adjust.
          resources:
            requests:
              cpu: 50m
              memory: 96Mi
            limits:
              cpu: 250m
              memory: 192Mi
      volumes:
        - name: scripts
          configMap:
            name: bootstrap-scripts
```

`k8s/overlays/aws-bootstrap/job-bootstrap-roles.yaml`: identical shape with these differences: `metadata.name: events-api-bootstrap-roles`, `serviceAccountName: events-api-bootstrap-roles`, container `name: bootstrap-roles`, `command: ["python", "/scripts/bootstrap_roles.py"]`.

- [ ] **Step 6: Rewrite the publications Job to read its values from `cluster-config`**

Replace the whole content of `k8s/overlays/aws-bootstrap/create-publications-job.yaml` (moved from `aws-cdc`; the inline hostname and region are gone):
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: aws-cdc-create-publications
  namespace: events-api
spec:
  suspend: true
  backoffLimit: 3
  template:
    spec:
      serviceAccountName: events-api-migrate
      restartPolicy: OnFailure
      initContainers:
        - name: generate-iam-token
          image: public.ecr.aws/aws-cli/aws-cli:latest
          command:
            - sh
            - -c
            - aws rds generate-db-auth-token --hostname "$PGHOST" --port 5432 --username events --region "$AWS_REGION" > /shared/pgpassword
          envFrom:
            - configMapRef:
                name: cluster-config
          env:
            - name: PGHOST
              valueFrom:
                configMapKeyRef:
                  name: cluster-config
                  key: RDS_HOST
          volumeMounts:
            - name: shared-token
              mountPath: /shared
      containers:
        - name: create-publications
          image: postgres:18
          command:
            - sh
            - -c
            - export PGPASSWORD=$(cat /shared/pgpassword) && psql -v ON_ERROR_STOP=1 -f /scripts/create-publications.sql
          env:
            - name: PGHOST
              valueFrom:
                configMapKeyRef:
                  name: cluster-config
                  key: RDS_HOST
            - name: PGUSER
              value: events
            - name: PGDATABASE
              value: events
            - name: PGSSLMODE
              value: require
          volumeMounts:
            - name: setup-scripts
              mountPath: /scripts
            - name: shared-token
              mountPath: /shared
      volumes:
        - name: setup-scripts
          configMap:
            name: aws-cdc-publications-sql
        - name: shared-token
          emptyDir: {}
```

- [ ] **Step 7: The overlay's kustomization**

`k8s/overlays/aws-bootstrap/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - service-accounts.yaml
  - job-bootstrap-master.yaml
  - job-bootstrap-roles.yaml
  - create-publications-job.yaml

configMapGenerator:
  - name: bootstrap-scripts
    namespace: events-api
    files:
      - scripts/bootstrap_master.py
      - scripts/bootstrap_roles.py
  - name: aws-cdc-publications-sql
    namespace: events-api
    files:
      - scripts/create-publications.sql

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```

- [ ] **Step 8: Check**

```bash
scripts/cluster/check-render.sh aws-bootstrap
kubectl kustomize k8s/overlays/aws-bootstrap 2>&1 | head -3
```
Acceptance: `ok   aws-bootstrap`. (The second command fails with the missing-env-file error, which is the intended safe failure without a generated `cluster-config.env`; confirm the message names that file.)

- [ ] **Step 9: Checkpoint (user runs)**

`git add -A k8s tests && git status --short`; commit `Add aws-bootstrap overlay: suspended rds_iam/role/publication Jobs`.

---

### Task 4: The `aws-seed` overlay

**Files:**
- Create: `k8s/overlays/aws-seed/{kustomization.yaml,job-seed.yaml}`, `k8s/overlays/aws-seed/scripts/seed.py`, `tests/unit/test_seed.py`

**Interfaces:**
- `seed.py` exposes `build_events(now: datetime, count: int) -> list[dict]` (tested) and calls the app's own API: `POST /admin/tenants` (body `{"name", "plan_tier"}`), `POST /events` with header `X-Tenant-ID` (body `{"event_type", "user_id", "occurred_at", "properties"}`), waiting on `GET /readyz`.
- Produces Job `events-api-seed`, applied until it has succeeded once per cluster lifetime (Task 5). A re-run after a partial failure adds a few extra demo tenants: acceptable for disposable seed data, and cheaper than making the seed transactional.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_seed.py`:
```python
"""Unit tests for the seed script's deterministic event builder."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "k8s/overlays/aws-seed/scripts/seed.py"


def load_script():
    spec = importlib.util.spec_from_file_location("seed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builds_the_requested_number_of_events_with_the_newest_at_now():
    module = load_script()
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    events = module.build_events(now, 12)

    assert len(events) == 12
    stamps = [datetime.fromisoformat(e["occurred_at"]) for e in events]
    assert max(stamps) == now  # freshness checks need at least one current event
    assert min(stamps) >= now - timedelta(days=3)


def test_events_use_the_api_field_names_and_more_than_one_event_type():
    module = load_script()
    events = module.build_events(datetime(2026, 9, 19, tzinfo=UTC), 12)

    assert {"event_type", "user_id", "occurred_at", "properties"} <= set(events[0])
    assert len({e["event_type"] for e in events}) > 1
```
```bash
uv run pytest tests/unit/test_seed.py -q
```
Expected: fails (script missing).

- [ ] **Step 2: Write `seed.py`**

`k8s/overlays/aws-seed/scripts/seed.py`:
```python
"""Seed data for a freshly recreated cluster. Runs until it has succeeded once
per cluster lifetime; re-running after a partial failure adds a few extra demo
tenants, which is acceptable for disposable seed data.

Goes through the app's own HTTP API instead of writing SQL, so the seed
exercises tenant creation, RLS (X-Tenant-ID -> app.current_tenant) and the
whole CDC path (Postgres -> Debezium -> Kafka -> Mongo projection, realtime
SSE, BigQuery sinks). Standard library only.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

BASE_URL = os.environ.get(
    "SEED_API_URL", "http://events-api.events-api.svc.cluster.local:8000"
)
TENANTS = [("Acme Analytics", "pro"), ("Globex Corp", "free")]
EVENT_TYPES = ["page_view", "signup", "purchase"]
EVENTS_PER_TENANT = 12


def build_events(now: datetime, count: int) -> list[dict]:
    """Deterministic events spread over the last three days; index 0 is `now`
    so freshness checks always see a current event."""
    events = []
    for i in range(count):
        events.append(
            {
                "event_type": EVENT_TYPES[i % len(EVENT_TYPES)],
                "user_id": f"user-{i % 4 + 1}",
                "occurred_at": (now - timedelta(days=i % 3, hours=i)).isoformat(),
                "properties": {"source": "seed", "sequence": i},
            }
        )
    events[0]["occurred_at"] = now.isoformat()
    return events


def request(method: str, path: str, body=None, tenant_id: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read() or b"null")


def wait_until_ready(timeout_seconds: int = 600) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            status, _ = request("GET", "/readyz")
            if status == 200:
                return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            raise SystemExit("the API never became ready")
        time.sleep(5)


def main() -> None:
    wait_until_ready()
    now = datetime.now(UTC)
    for name, plan_tier in TENANTS:
        _, tenant = request(
            "POST", "/admin/tenants", {"name": name, "plan_tier": plan_tier}
        )
        for event in build_events(now, EVENTS_PER_TENANT):
            request("POST", "/events", event, tenant_id=tenant["id"])
        print(f"seeded tenant {name} ({tenant['id']})")


if __name__ == "__main__":
    main()
```
```bash
uv run pytest tests/unit/test_seed.py -q
uv run ruff check k8s/overlays/aws-seed/scripts tests/unit/test_seed.py && uv run ruff format --check k8s/overlays/aws-seed/scripts tests/unit/test_seed.py
```
Acceptance: `2 passed`; ruff clean.

- [ ] **Step 3: Job and kustomization**

`k8s/overlays/aws-seed/job-seed.yaml`:
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: events-api-seed
  namespace: events-api
spec:
  # No ttlSecondsAfterFinished: a *succeeded* seed Job is what makes a
  # re-dispatched `up` skip seeding. A failed one is deleted and recreated by
  # stage_seed, so a failure never blocks later runs.
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: seed
          image: registry.placeholder.invalid/events-api-app:latest
          imagePullPolicy: Always
          command: ["python", "/scripts/seed.py"]
          volumeMounts:
            - name: scripts
              mountPath: /scripts
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 250m
              memory: 128Mi
      volumes:
        - name: scripts
          configMap:
            name: seed-scripts
```
`k8s/overlays/aws-seed/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - job-seed.yaml

configMapGenerator:
  - name: seed-scripts
    namespace: events-api
    files:
      - scripts/seed.py

components:
  - ../../components/cluster-config-source
  - ../../components/cluster-config-replacements
```
```bash
scripts/cluster/check-render.sh aws-seed
```
Acceptance: `ok   aws-seed`.

- [ ] **Step 4: Checkpoint (user runs)**

`git add -A k8s tests && git status --short`; commit `Add aws-seed overlay`.

---

### Task 5: The Kubernetes half of `up` and `down`

**Files:**
- Create: `scripts/cluster/lib.sh`, `scripts/cluster/render-config.sh`, `scripts/cluster/platform-up.sh`, `scripts/cluster/platform-down.sh`

**Interfaces:**
- `platform-up.sh` requires env `ACCOUNT_ID AWS_REGION RDS_HOST RDS_MASTER_SECRET_ARN CLUSTER_NAME` and a working kubeconfig; consumes every overlay/Job name defined in Tasks 1-4 and the Helm pins in Global Constraints.
- `platform-down.sh` requires only a working kubeconfig.

- [ ] **Step 1: `lib.sh`**

```bash
#!/usr/bin/env bash
# Shared helpers for scripts/cluster/*.sh. Sourced, never executed.

NAMESPACE="${NAMESPACE:-events-api}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
K8S_ROOT="${REPO_ROOT}/k8s"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_env() {
  local name
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || die "required environment variable ${name} is not set"
  done
}

# retry_until <timeout-seconds> <description> <command...>
# Re-runs the command every 10s until it succeeds; its output is discarded so
# nothing sensitive can leak through a probe.
retry_until() {
  local timeout="$1" what="$2" waited=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    if (( waited >= timeout )); then
      die "timed out after ${timeout}s waiting for ${what}"
    fi
    sleep 10
    waited=$(( waited + 10 ))
  done
}

# wait_job <name> <timeout-seconds>: returns when the Job completes; fails
# immediately (with the pod logs) if it fails, instead of waiting out the
# timeout the way `kubectl wait --for=condition=complete` would.
wait_job() {
  local name="$1" timeout="$2" waited=0
  while (( waited < timeout )); do
    if [[ "$(kubectl get job "${name}" -n "${NAMESPACE}" -o jsonpath='{.status.succeeded}')" == "1" ]]; then
      echo "job/${name} completed"
      return 0
    fi
    if [[ "$(kubectl get job "${name}" -n "${NAMESPACE}" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}')" == "True" ]]; then
      echo "job/${name} failed; last log lines:" >&2
      kubectl logs "job/${name}" -n "${NAMESPACE}" --all-containers --tail=50 >&2 || true
      return 1
    fi
    sleep 5
    waited=$(( waited + 5 ))
  done
  echo "job/${name} did not finish within ${timeout}s; last log lines:" >&2
  kubectl logs "job/${name}" -n "${NAMESPACE}" --all-containers --tail=50 >&2 || true
  return 1
}

# unsuspend_and_wait <job> <timeout-seconds>
unsuspend_and_wait() {
  kubectl patch job "$1" -n "${NAMESPACE}" --type merge -p '{"spec":{"suspend":false}}' >/dev/null
  wait_job "$1" "$2"
}

apply_overlay() {
  kubectl apply -k "${K8S_ROOT}/overlays/$1"
}
```

- [ ] **Step 2: `render-config.sh`**

```bash
#!/usr/bin/env bash
# Writes the (gitignored) env file that the cluster-config Kustomize
# component turns into the `cluster-config` ConfigMap. Every value comes from
# Terraform outputs handed in through the environment; nothing is hand-typed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_env ACCOUNT_ID AWS_REGION RDS_HOST RDS_MASTER_SECRET_ARN

target="${K8S_ROOT}/components/cluster-config-source/cluster-config.env"
cat > "${target}" <<EOF
ACCOUNT_ID=${ACCOUNT_ID}
ECR_REGISTRY=${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
RDS_HOST=${RDS_HOST}
AWS_REGION=${AWS_REGION}
RDS_MASTER_SECRET_ARN=${RDS_MASTER_SECRET_ARN}
EOF
log "wrote ${target}"
```

- [ ] **Step 3: `platform-up.sh`**

```bash
#!/usr/bin/env bash
# cluster-up, Kubernetes half. Idempotent: every stage can be re-run against a
# live cluster (the bootstrap-chain Jobs are deleted and re-applied; the seed
# Job is not).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${here}/lib.sh"

require_env ACCOUNT_ID AWS_REGION RDS_HOST RDS_MASTER_SECRET_ARN CLUSTER_NAME
HELM_TIMEOUT="${HELM_TIMEOUT:-10m}"

stage_config() {
  log "1/7 cluster-config and namespaces"
  "${here}/render-config.sh"
  local ns
  for ns in events-api monitoring; do
    kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
  done
}

stage_helm() {
  log "2/7 Helm releases and their CRDs"
  helm repo add eks https://aws.github.io/eks-charts >/dev/null
  helm repo add strimzi https://strimzi.io/charts/ >/dev/null
  helm repo add mongodb https://mongodb.github.io/helm-charts >/dev/null
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
  helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
  helm repo update >/dev/null

  # The ALB controller chart runs with serviceAccount.create=false, so its
  # IRSA-annotated ServiceAccount must exist before the chart is installed.
  apply_overlay aws-alb-controller

  helm upgrade --install metrics-server metrics-server/metrics-server \
    --version 3.14.0 -n kube-system \
    -f "${REPO_ROOT}/helm/metrics-server/values-override.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
    --version 3.5.0 -n kube-system \
    -f "${REPO_ROOT}/helm/aws-load-balancer-controller/values-override.yaml" \
    --set clusterName="${CLUSTER_NAME}" --set region="${AWS_REGION}" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm upgrade --install strimzi-kafka-operator strimzi/strimzi-kafka-operator \
    --version 1.2.0 -n events-api \
    -f "${REPO_ROOT}/helm/strimzi/values.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm upgrade --install community-operator mongodb/community-operator \
    --version 0.13.0 -n events-api \
    -f "${REPO_ROOT}/helm/mongodb-community-operator/values-override.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"
  helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
    --version 90.0.0 -n monitoring \
    -f "${REPO_ROOT}/helm/kube-prometheus-stack/values-override.yaml" \
    --wait --timeout "${HELM_TIMEOUT}"

  local crd
  for crd in kafkas.kafka.strimzi.io kafkanodepools.kafka.strimzi.io \
    kafkaconnects.kafka.strimzi.io kafkaconnectors.kafka.strimzi.io \
    mongodbcommunity.mongodbcommunity.mongodb.com; do
    kubectl wait --for=condition=Established "crd/${crd}" --timeout=120s
  done
}

ensure_mongo_seed_password() {
  if kubectl get secret mongo-consumer-seed-password -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi
  # Generated on the runner and piped straight into kubectl: never held in a
  # shell variable, never echoed. Only created when absent, so re-running `up`
  # against a live cluster never rotates the operator's user password.
  openssl rand -base64 24 | tr -d '\n' \
    | kubectl create secret generic mongo-consumer-seed-password \
      -n "${NAMESPACE}" --from-file=password=/dev/stdin
}

stage_base() {
  log "3/7 base workloads: app, Kafka, Mongo, realtime, dbt, observability"
  # The bootstrap-chain Jobs are recreated (suspended) on every run. Deleting
  # first means a completed Job's spec is never mutated by a re-apply.
  local job
  for job in events-api-migrate events-api-bootstrap-master \
    events-api-bootstrap-roles aws-cdc-create-publications; do
    kubectl delete job "${job}" -n "${NAMESPACE}" --ignore-not-found --wait=true
  done
  ensure_mongo_seed_password
  apply_overlay aws-realtime
  apply_overlay aws-dbt
  apply_overlay aws-observability
}

stage_bootstrap() {
  log "4/7 database bootstrap: rds_iam grant -> migration -> role setup -> publications"
  apply_overlay aws-bootstrap
  unsuspend_and_wait events-api-bootstrap-master 600
  unsuspend_and_wait events-api-migrate 900
  unsuspend_and_wait events-api-bootstrap-roles 600
  unsuspend_and_wait aws-cdc-create-publications 600
}

stage_connect() {
  log "5/7 credentials, Kafka Connect and connectors"
  # Debezium password: the canonical value lives in Secrets Manager (set by
  # the bootstrap-roles Job); pipe it into the Kubernetes Secret.
  aws secretsmanager get-secret-value \
    --secret-id events-api/debezium-replication --region "${AWS_REGION}" \
    --query SecretString --output text \
    | tr -d '\n' \
    | kubectl create secret generic debezium-db-credentials \
      -n "${NAMESPACE}" --from-file=password=/dev/stdin --dry-run=client -o yaml \
    | kubectl apply -f -

  # Mongo: the operator generates events-mongo-admin-cdc-consumer once the
  # replica set is up; the consumer reads a copy of its connection string.
  retry_until 1500 "the operator-generated Mongo connection Secret" \
    kubectl get secret events-mongo-admin-cdc-consumer -n "${NAMESPACE}"
  kubectl get secret events-mongo-admin-cdc-consumer -n "${NAMESPACE}" \
    -o jsonpath='{.data.connectionString\.standard}' \
    | base64 -d \
    | kubectl create secret generic streaming-mongo-credentials \
      -n "${NAMESPACE}" --from-file=STREAMING_MONGO_URI=/dev/stdin --dry-run=client -o yaml \
    | kubectl apply -f -

  apply_overlay aws-connect
  kubectl wait kafkaconnect/events-connect -n "${NAMESPACE}" \
    --for=condition=Ready --timeout=20m
}

stage_seed() {
  log "6/7 seed data (once per successful cluster lifetime)"
  # Gate on SUCCESS, not on existence: a Job that exists but failed (retries
  # exhausted) must be recreated, or every later `up` would die here until
  # someone deleted it by hand. A re-run after a partial failure can leave a
  # few extra demo tenants; acceptable for disposable seed data.
  if [[ "$(kubectl get job events-api-seed -n "${NAMESPACE}" -o jsonpath='{.status.succeeded}' 2>/dev/null)" == "1" ]]; then
    echo "seed already completed; not re-running"
    return 0
  fi
  kubectl delete job events-api-seed -n "${NAMESPACE}" --ignore-not-found --wait=true
  apply_overlay aws-seed
  wait_job events-api-seed 900
}

connectors_running() {
  local out
  out="$(kubectl get kafkaconnector -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.connectorStatus.connector.state}{" tasks="}{.status.connectorStatus.tasks[*].state}{"\n"}{end}')"
  [[ "$(grep -c 'RUNNING' <<<"${out}")" -ge 4 ]] \
    && ! grep -Eq 'FAILED|UNASSIGNED|PAUSED' <<<"${out}"
}

data_quality_ok() {
  kubectl exec deploy/events-api -n "${NAMESPACE}" -- python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/data-quality', timeout=10)"
}

alb_ok() {
  local host
  host="$(kubectl get ingress events-api -n "${NAMESPACE}" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
  [[ -n "${host}" ]] && curl -fsS --max-time 10 "http://${host}/healthz"
}

stage_verify() {
  log "7/7 verify: connectors, dbt, data quality, ALB"
  retry_until 900 "all 4 connectors RUNNING" connectors_running

  # /health/data-quality returns 503 until a dbt run has published a report
  # (fail-closed by design), so the run is required, not optional.
  local run="dbt-build-bootstrap-$(date +%s)"
  kubectl create job "${run}" --from=cronjob/dbt-build -n "${NAMESPACE}"
  wait_job "${run}" 900
  retry_until 300 "/health/data-quality to return 200" data_quality_ok

  retry_until 900 "the ALB to serve /healthz" alb_ok
  log "cluster-up verification passed"
}

stage_config
stage_helm
stage_base
stage_bootstrap
stage_connect
stage_seed
stage_verify
```

- [ ] **Step 4: `platform-down.sh`**

```bash
#!/usr/bin/env bash
# cluster-down, Kubernetes half. Order matters:
#  1. Ingress first, while the ALB controller still runs: it created the ALB
#     out-of-band, so Terraform has no record of it and would orphan it.
#  2. The Strimzi/Mongo custom resources next, while their operators still
#     run: their pods hold the PVCs, and a PVC in use stays Terminating.
#  3. Then the PVCs, so the EBS CSI driver releases the volumes.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "1/3 delete the Ingress and wait for the ALB controller to remove the ALB"
kubectl delete ingress events-api -n "${NAMESPACE}" --ignore-not-found --wait=true --timeout=10m

log "2/3 delete the Strimzi and Mongo custom resources"
for pair in \
  kafkaconnector:kafkaconnectors.kafka.strimzi.io \
  kafkaconnect:kafkaconnects.kafka.strimzi.io \
  kafka:kafkas.kafka.strimzi.io \
  kafkanodepool:kafkanodepools.kafka.strimzi.io \
  mongodbcommunity:mongodbcommunity.mongodbcommunity.mongodb.com; do
  kind="${pair%%:*}"
  crd="${pair#*:}"
  if kubectl get crd "${crd}" >/dev/null 2>&1; then
    kubectl delete "${kind}" --all -n "${NAMESPACE}" --ignore-not-found --wait=true --timeout=20m
  fi
done

log "3/3 delete PVCs"
kubectl delete pvc --all -n "${NAMESPACE}" --ignore-not-found --wait=true --timeout=10m
kubectl get pvc -A
```

- [ ] **Step 5: Syntax and lint**

```bash
chmod +x scripts/cluster/*.sh
for f in scripts/cluster/*.sh; do bash -n "$f" && echo "syntax ok: $f"; done
command -v shellcheck >/dev/null && shellcheck scripts/cluster/*.sh || echo "shellcheck not installed (brew install shellcheck); skipping"
```
Acceptance: `syntax ok` for all five scripts (including `check-render.sh`); shellcheck, if installed, reports nothing you cannot justify (SC1091 for the `source` of a relative path is acceptable; add `# shellcheck source=lib.sh` above the `source` lines if you want it silent).

- [ ] **Step 6: Checkpoint (user runs)**

`git add scripts && git status --short`; commit `Add cluster-up/down Kubernetes scripts`.

---

### Task 6: The two workflows

**Files:**
- Create: `.github/workflows/cluster-up.yaml`, `.github/workflows/cluster-down.yaml`

**Interfaces:**
- Consumes repo variables (already set by Milestone 10): `AWS_REGION`, `EKS_CLUSTER_NAME`, `AWS_TERRAFORM_APPLY_ROLE_ARN`; and a **new** repo variable `AWS_BOOTSTRAP_ROLE_ARN` (Step 1).

- [ ] **Step 1: Create the `AWS_BOOTSTRAP_ROLE_ARN` repository variable (user runs, once)**

```bash
ROLE_ARN=$(aws iam get-role --role-name events-api-github-bootstrap --profile events-api-tf --query 'Role.Arn' --output text)
gh variable set AWS_BOOTSTRAP_ROLE_ARN --body "${ROLE_ARN}" --repo viacheslavbinetskyiaws-ctrl/events-api
gh variable list --repo viacheslavbinetskyiaws-ctrl/events-api
```
Acceptance: the variable appears in the list. (If `gh` is not authenticated as `viacheslavbinetskyiaws-ctrl`, set it in GitHub: Settings -> Secrets and variables -> Actions -> Variables.)

- [ ] **Step 2: `cluster-up.yaml`**

```yaml
name: Cluster up

on:
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

# One lifecycle run at a time: up and down must never overlap.
concurrency:
  group: cluster-lifecycle
  cancel-in-progress: false

jobs:
  infra:
    runs-on: ubuntu-latest
    environment: aws-infra
    timeout-minutes: 90
    outputs:
      account_id: ${{ steps.out.outputs.account_id }}
      region: ${{ steps.out.outputs.region }}
      cluster_name: ${{ steps.out.outputs.cluster_name }}
      rds_host: ${{ steps.out.outputs.rds_host }}
      rds_master_secret_arn: ${{ steps.out.outputs.rds_master_secret_arn }}
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_TERRAFORM_APPLY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: hashicorp/setup-terraform@v4
        with:
          terraform_version: "1.15.8"
          # The wrapper changes stdout handling; `terraform output -raw` below
          # must be a clean value.
          terraform_wrapper: false
      - name: Apply the cluster root
        run: |
          cd terraform/cluster
          terraform init
          terraform apply -auto-approve
      - name: Export non-secret outputs for the platform job
        id: out
        run: |
          cd terraform/cluster
          {
            echo "account_id=$(terraform output -raw account_id)"
            echo "region=$(terraform output -raw region)"
            echo "cluster_name=$(terraform output -raw cluster_name)"
            echo "rds_host=$(terraform output -raw rds_address)"
            echo "rds_master_secret_arn=$(terraform output -raw rds_master_user_secret_arn)"
          } >> "${GITHUB_OUTPUT}"

  platform:
    needs: infra
    runs-on: ubuntu-latest
    # No `environment:` on purpose: the bootstrap role trusts the main-branch
    # sub claim only, which a job without an environment presents.
    timeout-minutes: 150
    env:
      ACCOUNT_ID: ${{ needs.infra.outputs.account_id }}
      AWS_REGION: ${{ needs.infra.outputs.region }}
      CLUSTER_NAME: ${{ needs.infra.outputs.cluster_name }}
      RDS_HOST: ${{ needs.infra.outputs.rds_host }}
      RDS_MASTER_SECRET_ARN: ${{ needs.infra.outputs.rds_master_secret_arn }}
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_BOOTSTRAP_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - name: Point kubectl at the new cluster
        run: aws eks update-kubeconfig --name "${CLUSTER_NAME}" --region "${AWS_REGION}"
      - name: Bring up the platform
        run: scripts/cluster/platform-up.sh
```

- [ ] **Step 3: `cluster-down.yaml`**

```yaml
name: Cluster down

on:
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

concurrency:
  group: cluster-lifecycle
  cancel-in-progress: false

jobs:
  platform:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_BOOTSTRAP_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - name: Does the cluster exist?
        id: exists
        run: |
          if aws eks describe-cluster --name "${{ vars.EKS_CLUSTER_NAME }}" >/dev/null 2>&1; then
            echo "exists=true" >> "${GITHUB_OUTPUT}"
          else
            echo "exists=false" >> "${GITHUB_OUTPUT}"
            echo "No cluster: skipping Kubernetes teardown."
          fi
      - name: Point kubectl at the cluster
        if: steps.exists.outputs.exists == 'true'
        run: aws eks update-kubeconfig --name "${{ vars.EKS_CLUSTER_NAME }}" --region "${{ vars.AWS_REGION }}"
      - name: Tear down the Kubernetes side
        if: steps.exists.outputs.exists == 'true'
        run: scripts/cluster/platform-down.sh

  infra:
    needs: platform
    runs-on: ubuntu-latest
    environment: aws-infra
    timeout-minutes: 90
    env:
      CLUSTER_NAME: ${{ vars.EKS_CLUSTER_NAME }}
    steps:
      - uses: actions/checkout@v7
      - uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_TERRAFORM_APPLY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
      - uses: hashicorp/setup-terraform@v4
        with:
          terraform_version: "1.15.8"
          terraform_wrapper: false
      - name: Refuse to destroy while an ALB still belongs to the cluster
        run: |
          for arn in $(aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text); do
            owner=$(aws elbv2 describe-tags --resource-arns "${arn}" \
              --query 'TagDescriptions[0].Tags[?Key==`elbv2.k8s.aws/cluster`].Value | [0]' --output text)
            if [ "${owner}" = "${CLUSTER_NAME}" ]; then
              echo "ALB ${arn} still belongs to ${CLUSTER_NAME}; it would be orphaned." >&2
              exit 1
            fi
          done
      - name: Destroy the cluster root
        run: |
          cd terraform/cluster
          terraform init
          terraform destroy -auto-approve
      - name: Assert nothing billable was orphaned
        # ASSUMPTION: this account holds only this project, so the load
        # balancer / NAT / EKS / RDS checks below are account-wide. If anything
        # else ever lives here, filter them by tag like the EBS check does.
        run: |
          fail=0
          check() { # check <description> <command output>
            if [ -n "$2" ] && [ "$2" != "None" ]; then echo "LEFTOVER $1: $2" >&2; fail=1; fi
          }
          check "cluster EBS volumes" "$(aws ec2 describe-volumes --filters "Name=tag:kubernetes.io/cluster/${CLUSTER_NAME},Values=owned" --query 'Volumes[].VolumeId' --output text)"
          check "load balancers" "$(aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName' --output text)"
          check "NAT gateways" "$(aws ec2 describe-nat-gateways --filter Name=state,Values=available,pending --query 'NatGateways[].NatGatewayId' --output text)"
          check "EKS clusters" "$(aws eks list-clusters --query 'clusters' --output text)"
          check "RDS instances" "$(aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' --output text)"
          exit ${fail}
```

- [ ] **Step 4: Lint (if available) and checkpoint**

```bash
command -v actionlint >/dev/null && actionlint .github/workflows/cluster-up.yaml .github/workflows/cluster-down.yaml || echo "actionlint not installed (brew install actionlint); the first dispatch is the check"
git add .github && git status --short
```
Commit `Add cluster-up and cluster-down workflows`. Push the branch and open the PR (the `test` job runs the two new unit-test files). **The workflows only appear in the Actions tab once merged to `main`**, and `bootstrap` can only be assumed from `main`, so the acceptance runs in Task 7 start after the merge.

---

### Task 7: Acceptance: the spec's verification bar, run for real

**Files:** none until Step 8.

- [ ] **Step 1: Preconditions**

```bash
git switch main && git pull
aws eks list-clusters --profile events-api-tf --region eu-central-1 --query clusters --output text
```
Acceptance: the PR from Task 6 is merged; the cluster list is empty (Plan 1 Task 8 Step 4 tore it down) or the cluster root is live from Plan 1 (either is fine: `cluster-up`'s `infra` job is idempotent). If `events-api-app`, `-streaming`, `-dbt`, `-realtime`, `-kafka-connect` images are not all present with a recent `latest`, wait for the merge's `CI` run to finish first.

- [ ] **Step 2: Bar 1: `cluster-up` from zero, no local commands**

GitHub -> Actions -> **Cluster up** -> Run workflow (branch `main`). Read it stage by stage. Expected iteration: this is the first real run of scripts that could not be executed locally; a failing stage stops the run with that stage's log. Fix the cause in the repo, merge, re-dispatch: every stage is idempotent.

Failure hints, from things verified or flagged during planning:
- `helm: command not found`: install it in the workflow before `platform-up.sh` (`azure/setup-helm`, pinned) or check the runner image's tooling.
- Kustomize errors mentioning `replacements`, `annotationSelector` or `components`: the runner's `kubectl` bundles an older Kustomize than the v5.8.1 the experiments used. Print `kubectl version --client` in the workflow and pin a newer kubectl (`azure/setup-kubectl`) if needed.
- `bootstrap-master` `AccessDenied` on `secretsmanager:GetSecretValue` mentioning KMS: add `kms:Decrypt` for the key in `modules/iam` (Plan 1 Task 4 Step 7 notes the secret uses the AWS-managed `aws/secretsmanager` key, so this is not expected).
- A bootstrap Job `OOMKilled`: raise its memory limit (the sizes are estimates).
- Pods `Pending` after stages 3-4: run `kubectl describe pod <name>` and look for `Insufficient memory`. Milestone 11's follow-up recorded a node at 93-105% requested memory and a reschedule that flipped from "fits" to "doesn't fit"; a cold `up` places the same workload set in a different order. The precedent fixes are in `WHATS_NEXT.md` (relocating the co-located `ebs-csi-controller` replicas; right-sizing over-requested components), so treat this as a known hint, not new design.
- `kafka-connect` image `exec format error`: the QEMU-built arm64 image is bad; see Plan 1 Task 6 Step 3.
- Connectors not RUNNING: read `kubectl get kafkaconnector -n events-api -o yaml` status messages; the ordering (publications + Debezium Secret before connectors) is already enforced.

Acceptance: both jobs green; the `platform` log ends with `cluster-up verification passed`.

- [ ] **Step 3: Bar 1, continued: deeper end-to-end evidence (user runs)**

The script checks connectors, dbt, `/health/data-quality` and the ALB. Also repeat the deeper checks Milestones 5 and 11 used, with the exact commands recorded in `WHATS_NEXT.md` (Mongo projection, both `realtime` SSE streams, both BigQuery sink tables): the seed events posted by the Job must appear in all of them. Also:
```bash
aws eks update-kubeconfig --name events-api-eks --region eu-central-1 --profile events-api-tf
kubectl get pods -A | grep -vE 'Running|Completed'
kubectl get kafkaconnector -n events-api
```
Acceptance: no `Pending`/`CrashLoopBackOff` pods (a header line only), four connectors `READY True`.

- [ ] **Step 4: Bar 4: re-dispatch on the live cluster is idempotent**

Record the seed Job's creation time, dispatch **Cluster up** again, and compare:
```bash
kubectl get job events-api-seed -n events-api -o jsonpath='{.metadata.creationTimestamp}{"\n"}'
```
Acceptance: after the second run the timestamp is unchanged (the seed did not re-run), the run is green, and the bootstrap Jobs were recreated (their creation times are new).

- [ ] **Step 5: Bar 2: `cluster-down`**

Dispatch **Cluster down**. Acceptance: both jobs green (its last step asserts no leftover EBS volumes, load balancers, NAT gateways, EKS clusters or RDS instances).

**If the `platform` job fails** (a PVC stuck `Terminating`, a Strimzi finalizer that outlasts its 20-minute timeout), the `infra` job is skipped on purpose (destroying with a live ALB would orphan it) and **EKS, RDS and the NAT gateway keep billing**. Read the failing step, clear the blocker (`kubectl describe` the stuck object; as a last resort remove its finalizer by hand), then re-dispatch **Cluster down**: `platform-down.sh` is idempotent. Do not add `continue-on-error` to the workflow; that would reintroduce the orphaned-ALB risk the ordering exists to prevent. As a final fallback once the ALB is confirmed gone, `AWS_PROFILE=events-api-tf terraform -chdir=terraform/cluster destroy` locally is equivalent to the `infra` job. Then confirm the persistent layer and both plans:
```bash
export AWS_PROFILE=events-api-tf AWS_REGION=eu-central-1
aws ecr describe-repositories --query 'repositories[].repositoryName' --output text
aws secretsmanager describe-secret --secret-id events-api/debezium-replication --query Name --output text
terraform -chdir=terraform plan
terraform -chdir=terraform/cluster plan
```
Acceptance: five ECR repos and the secret still exist; the foundation plan is `No changes.`; the cluster plan proposes a full create (nothing exists).

- [ ] **Step 6: Bar 3: a second `cluster-up` from zero**

Dispatch **Cluster up** again. Acceptance: green, and the Debezium password stayed stable across the cycle:
```bash
aws secretsmanager list-secret-version-ids --secret-id events-api/debezium-replication --profile events-api-tf --region eu-central-1 --query 'length(Versions)'
```
prints `1` (the value was generated once, on the first run ever, and reused; if it prints more than 1 the Job overwrote it and Job 2's first-run logic is wrong).

- [ ] **Step 7: Bar 5 and 6: existing pipelines and secret hygiene**

- Open a trivial PR touching `terraform/**`: both `plan` jobs run. Merge a trivial change to `main` and confirm the five-image `build-push` matrix; dispatch the existing **CI** `deploy` and confirm it still restarts the three Deployments.
- Skim the `cluster-up` run logs for any password/URI. Then:
```bash
terraform -chdir=terraform state pull | grep -ci 'debezium_replication' || true
terraform -chdir=terraform/cluster state pull | grep -c '"password"' || true
```
Acceptance: the logs show no secret values; the first command shows only the secret **container** (no value field); review the second's hits: RDS-managed password material must not appear (`manage_master_user_password = true` keeps it out of state).

- [ ] **Step 8: Update the docs (user runs, assistant drafts)**

- `AWS_PLAN.md`: add **Milestone 14: CI-driven cluster lifecycle (`cluster-up` / `cluster-down`)** referencing the spec, with the bar from Steps 2-7 as its Verification.
- `WHATS_NEXT.md`: a new entry recording, from real output, every plan count, the RDS-hostname result, the `kafka-connect` QEMU build result, the first-run failures and their fixes, and the GCP-root relocation.
- `NEXT_MILESTONE_PROMPT.md`: rewrite for whatever comes next; note that the manual-runbook items that remain are Debezium slot recovery and live-cluster password rotation (rotation = write a new Secrets Manager value, re-run the bootstrap-roles Job, restart Kafka Connect), and that GitOps is the named follow-on.
- `k8s/README.md`: state that AWS overlays now require a generated `cluster-config.env` (`scripts/cluster/render-config.sh`) and that `kubectl apply -k` without it fails on purpose.

## Findings from the first live run of `platform-up.sh` (2026-09-22)

Tasks 1-5 were rehearsed against a real, freshly created cluster before any workflow existed. Stages 1-5 passed on the first attempt; stages 6-7 surfaced three real issues, all fixed in the repo:

1. **Kafka broker `Pending` on a cold start** (`0/4 nodes are available: 1 node(s) had untolerated taint(s), 3 Insufficient memory ... Preemption is not helpful`). The three normal nodes had 519/639/301 Mi free against the broker's 720 Mi request, although 1.4 Gi was free in total: Prometheus, Grafana, the operators and Mongo had landed first, and every pod had the same default priority so nothing could be displaced. Fix: `k8s/overlays/aws-cdc/priority-class.yaml` (`events-data-plane`, value 1000) and `priorityClassName` on the KafkaNodePool. Verified live: the broker scheduled within 10 s, preempting exactly one 128 Mi app pod, which rescheduled and became Ready. (Also seen and deliberately not fixed here: both `ebs-csi-controller` replicas landed on one node again, the co-location pattern from Milestone 11.)
2. **`realtime` in `CrashLoopBackOff` after Kafka came up.** Pure consequence of starting before Kafka; Kubernetes waits up to 5 minutes between retries. `stage_verify` now runs `kubectl rollout status` for `events-api`, `realtime` and `cdc-consumer`, which waits that out and fails loudly if a Deployment never becomes available.
3. **dbt build Job failing: `PermissionError: [Errno 13] Permission denied: '/app/dbt/target'`.** Root cause, confirmed by inspecting the deployed image: `/app/dbt` (the WORKDIR) is `root:root` because `COPY --chown` owns only the copied files, and `target/` is gitignored so a CI checkout never has it. A laptop build hid it (a leftover local `dbt/target/` got copied in owned by `appuser`). Same family as the `dbt deps` bug. Isolated by re-running the identical Job as root: dbt build PASS=18, freshness PASS, `data_quality_runs updated: passed=True, 20 checks`, CloudWatch metric pushed, so permissions were the only problem. Fix: `RUN chown appuser:appuser /app/dbt` in the `runtime-dbt` stage of the `Dockerfile`; it takes effect once CI rebuilds the image.

Conveniences added to the scripts while iterating: `platform-up.sh` accepts stage names (`platform-up.sh helm base`), and fills missing `ACCOUNT_ID`/`AWS_REGION`/`CLUSTER_NAME`/`RDS_HOST`/`RDS_MASTER_SECRET_ARN` from the cluster root's Terraform outputs, so a local run is one command (`AWS_PROFILE=events-api-tf scripts/cluster/platform-up.sh`).

## Plan self-review (author's notes)

- **Spec coverage:** D5 (dynamic values) is Task 1-2 and `render-config.sh`; D6 (Debezium + Mongo credentials) is Task 3 and `stage_connect`; D7 (seed) is Task 4 and `stage_seed`; `up`'s eight stages map to `platform-up.sh` stages 1-7 plus the `infra` job (stage 1 in the spec is the `infra` job, so the script's stage numbers are offset by one); `down` is `platform-down.sh` plus the `infra` job's assertions; Verification bars 1-6 are Task 7 Steps 2-7.
- **Deviations from the approved spec, all found while planning and already folded into its Revisions section:** no `aws-cluster` Environment; three out-of-band Secrets; suspended-Job ordering (an implementation choice the spec left open); publications Job moved into `aws-bootstrap`.
- **Names used across tasks:** overlays `aws-bootstrap`, `aws-connect`, `aws-seed`; Jobs `events-api-bootstrap-master`, `events-api-bootstrap-roles`, `aws-cdc-create-publications`, `events-api-migrate`, `events-api-seed`; ConfigMaps `cluster-config`, `bootstrap-scripts`, `aws-cdc-publications-sql`, `seed-scripts`; env keys `ACCOUNT_ID`, `ECR_REGISTRY`, `RDS_HOST`, `AWS_REGION`, `RDS_MASTER_SECRET_ARN`.
- **Known unknowns the first real run resolves** (none is designed around by guessing): `helm` presence on the runner image; whether `kubectl wait` on `kafkaconnect` Ready has the status shape assumed; bootstrap Job memory sizing; QEMU build of the Kafka Connect image; whether `set -o pipefail` plus `kubectl create --from-file=/dev/stdin` behaves identically on the runner as locally.
