# AWS Milestone 3 (Strimzi Kafka, MongoDB Community Operator, RDS CDC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work.

**Goal:** Replace the kind migration's hand-rolled Kafka StatefulSet and
unauthenticated `mongo:7` with a Strimzi-managed Kafka cluster and a MongoDB
Community Operator-managed replica set on real EKS, wire Debezium CDC against
RDS (not self-managed Postgres), and prove the same `POST /events` → Kafka →
consumer → Mongo round trip already proven on kind now works end-to-end
against real AWS infrastructure.

**Architecture:** Terraform adds three infra pieces (RDS parameter group for
logical replication, EBS CSI driver addon + IRSA role, node group scale-up).
Everything Kubernetes-native from there is Helm CLI + committed manifests, not
Terraform: Strimzi Operator and MongoDB Community Operator installed via Helm,
a `Kafka`/`KafkaNodePool` CR for the broker, a custom-built `KafkaConnect`
image (Strimzi's own Kafka image + Debezium's Postgres connector plugin
layered in) managed via `KafkaConnect`/`KafkaConnector` CRs, and a
`MongoDBCommunity` CR for Mongo. A new `k8s/overlays/aws-cdc/` Kustomize
overlay (layered on `../aws`) holds everything Kustomize actually owns:
`KafkaConnector`s, the gp3 `StorageClass`, the publications job, and the
consumer Deployment repointed at Strimzi's bootstrap service and a new,
authenticated Mongo URI.

**Tech Stack:** Terraform (AWS provider ~6.62), Helm 3, Strimzi Kafka Operator
1.2.0 (Kafka 4.3.1, KRaft), MongoDB Community Operator 0.13.0, Debezium
3.0.0.Final's Postgres connector plugin, Kustomize.

**Spec:** `AWS_PLAN.md`'s "3. Real Kafka and MongoDB, the production-pattern
way" section (updated 2026-09-04 with this session's findings) — this plan
argues from that section; read it alongside this plan, not instead of it.

## Global Constraints

- Node architecture is `arm64` (Graviton `t4g.small`, the only free-tier-
  eligible type with enough memory — see `AWS_PLAN.md` Milestone 1). Every
  image referenced or built in this plan has been confirmed to ship
  `linux/arm64` manifests already (Strimzi operator/Kafka, all four MongoDB
  Community Operator images, `debezium/connect:3.0.0.Final`). Any *new* image
  pulled during implementation must be checked with
  `docker buildx imagetools inspect <image>` before relying on it — don't
  assume arch support.
- All real `aws` CLI calls need `--profile events-api-tf` — this account has
  no default profile configured.
- Every milestone gets its own `terraform apply`/`terraform destroy` cycle
  (`AWS_PLAN.md`'s own cost/teardown discipline). EKS + RDS + networking are
  currently still live from Milestones 1-2 (confirmed via `terraform state
  list` + live `aws eks describe-cluster`/`aws rds describe-db-instances`
  calls this session, not assumed) — this plan's Terraform tasks are
  incremental adds against that live state, not a from-scratch apply.
- Namespace is `events-api` throughout — no new namespace for Strimzi/Mongo,
  matching every existing overlay's convention.
- Nothing containing a real credential (RDS master password, the Mongo user's
  password) gets committed to git. Every Secret holding one is created
  imperatively via `kubectl create secret ... --from-literal=...`, never as a
  checked-in manifest with `stringData` — same convention as this repo's
  gitignored `terraform.tfvars`.
- Don't run `git commit` unless the user explicitly asks in that turn, even
  though steps below include the command — hand it over, don't run it
  autonomously.

---

## File Structure

**Terraform (all modified, none new):**
- `terraform/modules/rds/main.tf` — add `aws_db_parameter_group.logical_replication`, wire into `aws_db_instance.this.parameter_group_name`.
- `terraform/modules/eks/main.tf` — add EBS CSI driver IRSA role + `aws_eks_addon.ebs_csi`, bump node group `scaling_config.desired_size` 1→2.
- `terraform/modules/eks/outputs.tf` — no change needed (nothing downstream needs a new output for this).
- `terraform/modules/ecr/variables.tf` — add `"kafka-connect"` to `repository_names`' default list.

**New: `kafka-connect/` (own directory, mirrors `realtime/`'s "nothing to share with the Python builder stage" reasoning):**
- `kafka-connect/Dockerfile` — two-stage build: `FROM debezium/connect:3.0.0.Final AS debezium-plugins`, then `FROM quay.io/strimzi/kafka:1.2.0-kafka-4.3.1` with the Postgres connector plugin copied in.

**New: `helm/strimzi/values.yaml`** — Helm values override for the `strimzi-kafka-operator` chart (mirrors `helm/bitnami-postgres/values-override.yaml`'s precedent of committing the real values file used).

**New: `k8s/overlays/aws-cdc/`** (layers on `../aws`, same chaining pattern `k8s/overlays/realtime` uses on `../cdc`):
- `kustomization.yaml`
- `storage-class.yaml` — the `gp3` `StorageClass`.
- `kafka-cluster.yaml` — `KafkaNodePool` + `Kafka` CRs.
- `kafka-connect.yaml` — `KafkaConnect` CR.
- `kafka-connectors.yaml` — two `KafkaConnector` CRs (tenant-accounts, events).
- `mongodb-community.yaml` — `MongoDBCommunity` CR.
- `consumer-deployment.yaml` — the CDC consumer Deployment, repointed at Strimzi's bootstrap service and the new Mongo Secret (replaces `k8s/overlays/cdc/cdc-consumer-deployment.yaml` for this overlay instead of inheriting it, since the env wiring changes).
- `create-publications-job.yaml` + `scripts/create-publications.sql` — adapted from `k8s/overlays/cdc/scripts/create-publications.sql`, retargeted at the RDS hostname.

**Modified: `terraform/main.tf`, `terraform/outputs.tf`** — no change needed; `ecr_repository_urls` already surfaces the new repo once the variable list changes, and `rds_master_user_secret_arn` already exists for the credential-retrieval steps below.

No changes needed to `streaming/mongo.py` or `streaming/config.py` — `AsyncMongoClient(settings.mongo_uri)` already accepts any valid connection string, including credentials and `?replicaSet=`. Only the `STREAMING_MONGO_URI` *value* changes, and it moves from a plain `ConfigMap` (kind's unauthenticated Mongo had no password to protect) to a `Secret`, since the AWS value embeds a password.

---

### Task 1: RDS Parameter Group for logical replication

**Files:**
- Modify: `terraform/modules/rds/main.tf`

**Interfaces:**
- Produces: `aws_db_parameter_group.logical_replication` (referenced by `aws_db_instance.this.parameter_group_name` in the same file).

- [ ] **Step 1: Add the parameter group resource**

In `terraform/modules/rds/main.tf`, add before the `aws_db_instance "this"` block:

```hcl
resource "aws_db_parameter_group" "logical_replication" {
  name   = "${var.name_prefix}-postgres18-logical-replication"
  family = "postgres18"

  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }
}
```

`family = "postgres18"` is confirmed against the live account, not assumed:
`aws rds describe-db-engine-versions --engine postgres --default-only
--region eu-central-1 --profile events-api-tf --query
'DBEngineVersions[0].[EngineVersion,DBParameterGroupFamily]' --output text`
returned `18.3  postgres18`, matching the actually-running `events-api-db`
instance's engine version.

- [ ] **Step 2: Wire the parameter group into the DB instance**

In the same file, add one line inside `resource "aws_db_instance" "this"`:

```hcl
  parameter_group_name = aws_db_parameter_group.logical_replication.name
```

- [ ] **Step 3: Plan and review**

Run: `cd terraform && AWS_PROFILE=events-api-tf terraform plan`

Expected: `1 to add` (the parameter group), `1 to change` (the DB instance,
in-place — associating a parameter group doesn't force replacement). If it
shows a replacement instead, stop and investigate before applying — that
would mean an unexpected forced-new-resource on a live, real database.

- [ ] **Step 4: Apply**

Run: `AWS_PROFILE=events-api-tf terraform apply` (from `terraform/`)

- [ ] **Step 5: Reboot — required, this is a static parameter**

`rds.logical_replication` only takes effect after a reboot (RDS has no live
`SET`, unlike self-managed Postgres). Run:

```bash
aws rds reboot-db-instance --db-instance-identifier events-api-db \
  --profile events-api-tf --region eu-central-1
```

Then poll until it's back:

```bash
aws rds describe-db-instances --db-instance-identifier events-api-db \
  --profile events-api-tf --region eu-central-1 \
  --query 'DBInstances[0].DBInstanceStatus' --output text
```

Expected: `rebooting` then `available` (typically 2-5 minutes).

- [ ] **Step 6: Verify `wal_level = logical` from inside the cluster**

RDS is only reachable from within the VPC (the `db_postgres` ingress rule
only allows the EKS cluster's security group). Fetch the master password and
run a one-off pod:

```bash
MASTER_PW=$(aws secretsmanager get-secret-value \
  --secret-id "$(cd terraform && AWS_PROFILE=events-api-tf terraform output -raw rds_master_user_secret_arn)" \
  --profile events-api-tf --region eu-central-1 \
  --query SecretString --output text | jq -r .password)

kubectl run -n events-api pg-check --rm -it --restart=Never \
  --image postgres:17 --env="PGPASSWORD=$MASTER_PW" -- \
  psql "postgresql://events@events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com:5432/events?sslmode=require" \
  -c "SHOW wal_level;"
```

Expected output includes `logical`.

---

### Task 2: EBS CSI driver addon + node group scale-up

**Files:**
- Modify: `terraform/modules/eks/main.tf`

**Interfaces:**
- Consumes: `aws_iam_openid_connect_provider.cluster` (already exists in this file, from Milestone 1).
- Produces: `aws_eks_addon.ebs_csi` (nothing downstream references it by name — verified by existence/status, not wired to other resources).

- [ ] **Step 1: Add the EBS CSI driver's IRSA role**

In `terraform/modules/eks/main.tf`, add (this mirrors `modules/iam/main.tf`'s
`irsa_trust` pattern, kept local to this module since it's addon-specific
plumbing, not "the app's own AWS identity" `modules/iam` is scoped to):

```hcl
data "aws_iam_policy_document" "ebs_csi_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.cluster.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")}:sub"
      values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(aws_iam_openid_connect_provider.cluster.url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name_prefix}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.ebs_csi_irsa_trust.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}
```

Note the policy ARN's `service-role/` path — this is the actual path for
this managed policy, not the bare `arn:aws:iam::aws:policy/` prefix most
other attachments in this repo use.

- [ ] **Step 2: Add the addon itself**

```hcl
resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = aws_eks_cluster.this.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs_csi.arn

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"
}
```

(`resolve_conflicts_on_create`/`_on_update` — the v5→v6 provider split
already logged in Milestone 1's own notes.)

- [ ] **Step 3: Bump node group size**

In the same file, in `resource "aws_eks_node_group" "this"`'s
`scaling_config` block, change:

```hcl
  scaling_config {
    desired_size = 2
    min_size     = 1
    max_size     = 2
  }
```

(`desired_size` only — `max_size` stays 2, already enough headroom per this
session's capacity math in `AWS_PLAN.md`.)

**Real finding, confirmed while executing Task 11**: 2 nodes turned out *not*
to be enough after all. The MongoDB Community Operator sets its own default
resource requests on the two containers it puts in each pod (`mongod`
500m/400M, `mongodb-agent` 500m/400M — neither specified in the
`MongoDBCommunity` CR, entirely the operator's own default) — 1000m CPU /
~800Mi memory for one pod, needing to land on a single node (Kubernetes can't
split one pod's containers across nodes). By the time Task 11 was reached,
both nodes were already at 87-98% memory requests from Kafka/Connect/Strimzi/
the Mongo operator itself, with nowhere near 800Mi headroom on either.
`FailedScheduling: 1 Insufficient cpu, 2 Insufficient memory` confirmed this
directly. Node group had to go to 3 (`desired_size`/`max_size` both bumped in
a second Terraform apply, not caught in the original planning pass) — see
Task 11's own note for the exact numbers. Worth remembering for future
capacity estimates in this project: an Operator's own default container
requests can exceed a hand-estimated budget by a wide margin (planning
guessed ~450m/712Mi for the whole Mongo pod; actual was 1000m/800Mi) — check
the actual scheduled pod's `resources` empirically rather than trusting a
pre-apply estimate, the same lesson this project has already learned
repeatedly elsewhere (verify against real behavior, not assumption).

- [ ] **Step 4: Plan, review, apply**

Run: `AWS_PROFILE=events-api-tf terraform plan` (from `terraform/`)

Expected: `3 to add` (IRSA role, policy attachment, addon), `1 to change`
(node group, in-place scaling — not a replacement).

Run: `AWS_PROFILE=events-api-tf terraform apply`

- [ ] **Step 5: Verify**

```bash
aws eks describe-addon --cluster-name events-api-eks --addon-name aws-ebs-csi-driver \
  --profile events-api-tf --region eu-central-1 --query 'addon.status' --output text
```

Expected: `ACTIVE`.

```bash
kubectl get nodes
```

Expected: 2 `Ready` nodes.

```bash
kubectl -n kube-system get pods -l app.kubernetes.io/name=aws-ebs-csi-driver
```

Expected: an `ebs-csi-controller` Deployment (2 replicas) and an
`ebs-csi-node` DaemonSet pod per node, all `Running`.

---

### Task 3: `gp3` StorageClass, proven with a throwaway PVC

**Files:**
- Create: `k8s/overlays/aws-cdc/storage-class.yaml`

**Interfaces:**
- Produces: a `StorageClass` named `gp3`, referenced by name (`class: gp3` / `storageClassName: gp3`) from Tasks 5 and 11's CRs.

- [ ] **Step 1: Write the StorageClass**

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
```

`WaitForFirstConsumer` matters here specifically: EBS volumes are AZ-bound,
and this cluster's nodes span multiple private subnets/AZs — immediate
binding risks provisioning a volume in an AZ with no schedulable node for it.
Deliberately not marked as the cluster's default `StorageClass` — EKS already
ships a default `gp2`, and two defaults is an error state.

- [ ] **Step 2: Apply directly (this overlay's kustomization doesn't exist yet — apply standalone for now)**

```bash
kubectl apply -f k8s/overlays/aws-cdc/storage-class.yaml
```

- [ ] **Step 3: Prove it actually provisions, with a throwaway PVC**

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: gp3-smoke-test
  namespace: events-api
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: gp3
  resources:
    requests:
      storage: 1Gi
EOF
```

This won't bind immediately (`WaitForFirstConsumer` — expected, not a bug).
Force scheduling with a pod that mounts it:

```bash
kubectl run -n events-api gp3-smoke-pod --restart=Never --image=busybox \
  --overrides='{"spec":{"containers":[{"name":"gp3-smoke-pod","image":"busybox","command":["sleep","60"],"volumeMounts":[{"name":"v","mountPath":"/data"}]}],"volumes":[{"name":"v","persistentVolumeClaim":{"claimName":"gp3-smoke-test"}}]}}'
```

```bash
kubectl -n events-api get pvc gp3-smoke-test
```

Expected: `Bound`. Then clean up:

```bash
kubectl -n events-api delete pod gp3-smoke-pod
kubectl -n events-api delete pvc gp3-smoke-test
```

---

### Task 4: Strimzi Cluster Operator via Helm

**Files:**
- Create: `helm/strimzi/values.yaml`

**Interfaces:**
- Produces: the Strimzi Cluster Operator watching the `events-api` namespace — Task 5's `Kafka`/`KafkaNodePool` CRs and Task 7's `KafkaConnect` CR are reconciled by it.

- [ ] **Step 1: Add the Helm repo**

```bash
helm repo add strimzi https://strimzi.io/charts/
helm repo update
```

- [ ] **Step 2: Write the values override**

```yaml
# helm/strimzi/values.yaml
watchNamespaces:
  - events-api
```

(Everything else stays at chart defaults — the operator's own default
requests/limits, `200m`/`384Mi` requesting, are already fine for this
cluster's capacity budget.)

- [ ] **Step 3: Install**

```bash
helm install strimzi-kafka-operator strimzi/strimzi-kafka-operator \
  --version 1.2.0 \
  --namespace events-api \
  -f helm/strimzi/values.yaml
```

- [ ] **Step 4: Verify**

```bash
kubectl -n events-api get pods -l name=strimzi-cluster-operator
```

Expected: one pod, `Running`, `1/1 Ready`.

---

### Task 5: Kafka cluster (`KafkaNodePool` + `Kafka` CRs)

**Files:**
- Create: `k8s/overlays/aws-cdc/kafka-cluster.yaml`

**Interfaces:**
- Consumes: the `gp3` StorageClass (Task 3), the Strimzi Operator (Task 4).
- Produces: bootstrap service `events-kafka-bootstrap:9092` — every later
  reference to "the Kafka broker" (KafkaConnect's `bootstrapServers`, the
  consumer's env var) uses this exact hostname:port.

- [ ] **Step 1: Write the CRs**

Grounded directly against Strimzi 1.2.0's own single-node KRaft example
(`examples/kafka/kafka-single-node.yaml` in the `strimzi/strimzi-kafka-
operator` repo at tag `1.2.0`), adapted for `gp3` and this project's naming:

```yaml
apiVersion: kafka.strimzi.io/v1
kind: KafkaNodePool
metadata:
  name: dual-role
  labels:
    strimzi.io/cluster: events
  namespace: events-api
spec:
  replicas: 1
  roles:
    - controller
    - broker
  resources:
    requests:
      cpu: 250m
      memory: 512Mi
    limits:
      cpu: 1000m
      memory: 1Gi
  storage:
    type: jbod
    volumes:
      - id: 0
        type: persistent-claim
        size: 5Gi
        class: gp3
        kraftMetadata: shared
---
apiVersion: kafka.strimzi.io/v1
kind: Kafka
metadata:
  name: events
  namespace: events-api
spec:
  kafka:
    version: 4.3.1
    metadataVersion: 4.3-IV0
    listeners:
      - name: plain
        port: 9092
        type: internal
        tls: false
    config:
      offsets.topic.replication.factor: 1
      transaction.state.log.replication.factor: 1
      transaction.state.log.min.isr: 1
      default.replication.factor: 1
      min.insync.replicas: 1
```

No `entityOperator` block — Topic/User CRDs aren't needed (Kafka's own
broker-level auto-topic-creation, already relied on by the existing
consumer, is unrelated to the Entity Operator and works without it), and
skipping it saves a pod against this cluster's tight capacity budget.

- [ ] **Step 2: Apply directly**

```bash
kubectl apply -f k8s/overlays/aws-cdc/kafka-cluster.yaml
```

- [ ] **Step 3: Verify**

```bash
kubectl -n events-api get kafka events -w
```

Expected: `READY` becomes `True` (can take a few minutes on first apply —
KRaft metadata bootstrap).

```bash
kubectl -n events-api get pvc
```

Expected: a bound PVC for the broker with `STORAGECLASS` = `gp3`.

```bash
kubectl -n events-api get svc events-kafka-bootstrap
```

Expected: exists, port `9092`.

---

### Task 6: Custom Kafka Connect image (Debezium plugin on Strimzi's base)

**Files:**
- Create: `kafka-connect/Dockerfile`
- Modify: `terraform/modules/ecr/variables.tf`

**Interfaces:**
- Produces: an image pushed to ECR at `<account>.dkr.ecr.eu-central-1.amazonaws.com/events-api-kafka-connect:latest`, referenced by Task 7's `KafkaConnect.spec.image`.

- [ ] **Step 1: Add the ECR repo**

In `terraform/modules/ecr/variables.tf`, change the default list:

```hcl
variable "repository_names" {
  type    = list(string)
  default = ["app", "streaming", "dbt", "realtime", "kafka-connect"]
}
```

Run: `cd terraform && AWS_PROFILE=events-api-tf terraform apply`

Expected: `1 to add` (the new `aws_ecr_repository`).

- [ ] **Step 2: Write the Dockerfile**

Plugin path confirmed empirically (not assumed) via
`docker run --rm --platform linux/arm64 debezium/connect:3.0.0.Final sh -c
'ls /kafka/connect/'`, which lists `debezium-connector-postgres` among
other connector directories, and Strimzi's own base image ships an empty
`/opt/kafka/plugins` directory as its documented custom-plugin location.

```dockerfile
FROM debezium/connect:3.0.0.Final AS debezium-plugins

FROM quay.io/strimzi/kafka:1.2.0-kafka-4.3.1
USER root:root
COPY --from=debezium-plugins --chown=1001:0 \
  /kafka/connect/debezium-connector-postgres \
  /opt/kafka/plugins/debezium-connector-postgres
USER 1001
```

(`--chown=1001:0` — Strimzi's images run as non-root UID `1001`/group `0`;
without this the copied plugin files would keep the source image's
ownership and might not be readable by the runtime user.)

- [ ] **Step 3: Build and push**

```bash
ECR_URL=$(cd terraform && AWS_PROFILE=events-api-tf terraform output -json ecr_repository_urls | jq -r '."kafka-connect"')

aws ecr get-login-password --profile events-api-tf --region eu-central-1 | \
  docker login --username AWS --password-stdin "${ECR_URL%/*}"

docker build -t "${ECR_URL}:latest" kafka-connect/
docker push "${ECR_URL}:latest"
```

Note the braces around `${ECR_URL}` — plain `"$ECR_URL:latest"` is a real zsh
trap: zsh parses `:l` right after a bare `$VAR` as a history-style modifier
("lowercase the value"), silently consuming the colon and the `l` and
appending the literal remainder (`atest`) straight onto the value — producing
a mangled repository name with no error until `docker push` fails against a
repo that was never actually created. Confirmed empirically
(`zsh -c 'X=foo; echo "$X:latest"'` → `fooatest`; `echo "${X}:latest"` →
`foo:latest`). Braces sidestep it by unambiguously ending the parameter
reference before the colon.

No `--platform` flag — this Mac is Apple Silicon, so a bare `docker build`
already produces `linux/arm64`, matching Milestone 1's own established
precedent for this repo's other images.

- [ ] **Step 4: Verify**

```bash
docker buildx imagetools inspect "${ECR_URL}:latest" | grep Platform
```

Expected: `linux/arm64` present.

```bash
docker run --rm --platform linux/arm64 --entrypoint ls "${ECR_URL}:latest" /opt/kafka/plugins/debezium-connector-postgres
```

Expected: a list of `.jar` files, confirming the plugin actually landed
where Strimzi expects it.

---

### Task 7: `KafkaConnect` CR

**Files:**
- Create: `k8s/overlays/aws-cdc/kafka-connect.yaml`

**Interfaces:**
- Consumes: `events-kafka-bootstrap:9092` (Task 5), the ECR image from Task 6.
- Produces: a `KafkaConnect` instance named `events-connect` that Task 9's
  `KafkaConnector` CRs attach to via `strimzi.io/cluster: events-connect`,
  and an `EnvVarConfigProvider` wired to read `DEBEZIUM_DB_PASSWORD` from
  whatever Secret Task 8 creates.

- [ ] **Step 1: Write the CR**

Grounded against Strimzi 1.2.0's own docs
(`documentation/modules/configuring/proc-loading-config-from-env-vars.adoc`)
for the exact `EnvVarConfigProvider` wiring — this is what lets Task 9's
`KafkaConnector`s reference `${env:DEBEZIUM_DB_PASSWORD}` instead of
embedding the real RDS master password in a committed manifest.

```yaml
apiVersion: kafka.strimzi.io/v1
kind: KafkaConnect
metadata:
  name: events-connect
  namespace: events-api
  annotations:
    strimzi.io/use-connector-resources: "true"
spec:
  image: REPLACE_WITH_ECR_URL:latest
  replicas: 1
  bootstrapServers: events-kafka-bootstrap:9092
  groupId: events-connect-cluster
  configStorageTopic: events-connect-configs
  statusStorageTopic: events-connect-status
  offsetStorageTopic: events-connect-offsets
  config:
    config.storage.replication.factor: 1
    offset.storage.replication.factor: 1
    status.storage.replication.factor: 1
    config.providers: env
    config.providers.env.class: org.apache.kafka.common.config.provider.EnvVarConfigProvider
  template:
    connectContainer:
      env:
        - name: DEBEZIUM_DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: debezium-db-credentials
              key: password
```

`REPLACE_WITH_ECR_URL` gets the real value substituted in — see the note in
Task 12 about how this overlay's `kustomization.yaml` handles this the same
way `k8s/overlays/aws/deployment-patch.yaml` already hardcodes its own real
ECR URL literally (this repo's established convention: real, stable AWS
identifiers get checked in literally, not templated — the account ID and
region don't change between applies).

`debezium-db-credentials` is the Secret Task 8 creates imperatively — this
CR only references its *name*, never its value, so the CR itself is safe to
commit even though the Secret it points at never is.

- [ ] **Step 2: Apply (after Task 8's Secret exists — the pod won't start without it)**

Hold off applying until Task 8 is done; then:

```bash
kubectl apply -f k8s/overlays/aws-cdc/kafka-connect.yaml
```

- [ ] **Step 3: Verify**

```bash
kubectl -n events-api get kafkaconnect events-connect -w
```

Expected: `READY` becomes `True`.

---

### Task 8: RDS-side CDC prep (publications + Debezium credential)

**Files:**
- Create: `k8s/overlays/aws-cdc/scripts/create-publications.sql`
- Create: `k8s/overlays/aws-cdc/create-publications-job.yaml`

**Interfaces:**
- Produces: two Postgres publications on RDS (`dbz_tenant_accounts_publication`, `dbz_events_publication`) and the `debezium-db-credentials` Secret Task 7 references.

- [ ] **Step 1: Copy and retarget the publications SQL**

Identical content to `k8s/overlays/cdc/scripts/create-publications.sql` (no
change needed to the SQL itself — publications aren't host-specific):

```sql
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'dbz_tenant_accounts_publication') THEN
        CREATE PUBLICATION dbz_tenant_accounts_publication FOR TABLE public.tenant_accounts;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'dbz_events_publication') THEN
        CREATE PUBLICATION dbz_events_publication FOR TABLE public.events;
    END IF;
END
$$;
```

- [ ] **Step 2: Write the one-off Job, pointed at RDS**

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: aws-cdc-create-publications
  namespace: events-api
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: create-publications
          image: postgres:17
          command: ["psql", "-v", "ON_ERROR_STOP=1", "-f", "/scripts/create-publications.sql"]
          env:
            - name: PGHOST
              value: events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com
            - name: PGUSER
              value: events
            - name: PGDATABASE
              value: events
            - name: PGSSLMODE
              value: require
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: rds-master-credentials
                  key: password
          volumeMounts:
            - name: setup-scripts
              mountPath: /scripts
      volumes:
        - name: setup-scripts
          configMap:
            name: aws-cdc-publications-sql
```

This references a `rds-master-credentials` Secret and an
`aws-cdc-publications-sql` ConfigMap, created next — this Job is applied via
this overlay's `kustomization.yaml`'s `configMapGenerator` in Task 12, not
standalone, since the ConfigMap needs generating from the real file.

- [ ] **Step 3: Create the master-password Secret (imperative, not committed)**

```bash
MASTER_PW=$(aws secretsmanager get-secret-value \
  --secret-id "$(cd terraform && AWS_PROFILE=events-api-tf terraform output -raw rds_master_user_secret_arn)" \
  --profile events-api-tf --region eu-central-1 \
  --query SecretString --output text | jq -r .password)

kubectl create secret generic rds-master-credentials \
  --namespace events-api \
  --from-literal=password="$MASTER_PW"
```

- [ ] **Step 4: Verify the master user can actually replicate, before wiring Debezium to it**

```bash
kubectl run -n events-api pg-repl-check --rm -it --restart=Never \
  --image postgres:17 --env="PGPASSWORD=$MASTER_PW" -- \
  psql "postgresql://events@events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com:5432/events?sslmode=require" \
  -c "SELECT pg_has_role('events', 'rds_replication', 'MEMBER');"
```

Expected: `t`. **If this comes back `f`**, RDS's master user does not
automatically carry `rds_replication` here — run
`GRANT rds_replication TO events;` via the same one-off pod before
proceeding to Task 9's connectors, which will otherwise fail with a
permissions error identical in shape to Milestone 10's
`InsufficientPrivilegeError` finding.

- [ ] **Step 5: Create the Debezium credential Secret (same password, separate Secret name — matches Task 7's reference)**

```bash
kubectl create secret generic debezium-db-credentials \
  --namespace events-api \
  --from-literal=password="$MASTER_PW"
```

(Reusing the master password rather than creating a narrower-privileged
Postgres role is a deliberate scope call for this milestone — Debezium's
connector needs `rds_replication` either way, and creating a dedicated
least-privilege replication role is a real improvement but not what this
milestone's own verification bar requires. Worth flagging as a follow-up,
not silently skipping.)

- [ ] **Step 6: Create the publications now, via a one-off pod — don't wait for Task 12**

**Sequencing bug found while executing this plan**: the original draft
deferred running `create-publications.sql` to Task 12, since that's where the
overlay's `configMapGenerator` wires the file into a real Job. But Task 9's
connectors run *before* Task 12 in this plan's own ordering, and they rely on
`publication.autocreate.mode: filtered` as a fallback — which has the known
Debezium 3.0.0.Final bug already documented in `k8s/overlays/cdc/scripts/
create-publications.sql`'s own comment
(`DebeziumException: No table filters found for filtered publication`).
Following the original ordering literally would mean Task 9 fails.

Fix: run the publications SQL immediately via a one-off pod (same shape as
Task 1's `wal_level` verification pod), using the master credentials already
fetched in Step 2. The SQL is already idempotent (`IF NOT EXISTS` guards), so
Task 12's Job running the identical script again later is harmless — it'll
just no-op. Both the immediate correctness (unblocking Task 9) and the
long-term reproducibility goal (a real Job committed for a from-scratch
rebuild) are preserved.

```bash
kubectl run -n events-api create-publications --rm -it --restart=Never \
  --image postgres:17 --env="PGPASSWORD=$MASTER_PW" -- \
  psql "postgresql://events@events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com:5432/events?sslmode=require" \
  -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'dbz_tenant_accounts_publication') THEN CREATE PUBLICATION dbz_tenant_accounts_publication FOR TABLE public.tenant_accounts; END IF; END \$\$;" \
  -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'dbz_events_publication') THEN CREATE PUBLICATION dbz_events_publication FOR TABLE public.events; END IF; END \$\$;"
```

Verify both exist:

```bash
kubectl run -n events-api pub-check --rm -it --restart=Never \
  --image postgres:17 --env="PGPASSWORD=$MASTER_PW" -- \
  psql "postgresql://events@events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com:5432/events?sslmode=require" \
  -c "SELECT pubname FROM pg_publication;"
```

Expected: both `dbz_tenant_accounts_publication` and `dbz_events_publication`
listed. Task 12's Job (still built as originally planned, for a from-scratch
rebuild) becomes a redundant-but-harmless re-run of this same idempotent SQL,
not the only time it ever runs.

---

### Task 9: `KafkaConnector` CRs (tenant-accounts, events)

**Files:**
- Create: `k8s/overlays/aws-cdc/kafka-connectors.yaml`

**Interfaces:**
- Consumes: `events-connect` (Task 7), the publications from Task 8.
- Produces: topics `cdc.public.tenant_accounts` and `cdc.public.events` — Task 12's consumer Deployment subscribes to these exact names, unchanged from the kind migration.

- [ ] **Step 1: Write both connector CRs**

```yaml
apiVersion: kafka.strimzi.io/v1
kind: KafkaConnector
metadata:
  name: tenant-accounts-connector
  namespace: events-api
  labels:
    strimzi.io/cluster: events-connect
spec:
  class: io.debezium.connector.postgresql.PostgresConnector
  tasksMax: 1
  config:
    database.hostname: events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com
    database.port: "5432"
    database.user: events
    database.password: "${env:DEBEZIUM_DB_PASSWORD}"
    database.dbname: events
    database.sslmode: require
    topic.prefix: cdc
    table.include.list: public.tenant_accounts
    plugin.name: pgoutput
    slot.name: debezium_tenant_accounts
    publication.name: dbz_tenant_accounts_publication
    publication.autocreate.mode: filtered
    key.converter: org.apache.kafka.connect.json.JsonConverter
    key.converter.schemas.enable: "false"
    value.converter: org.apache.kafka.connect.json.JsonConverter
    value.converter.schemas.enable: "false"
---
apiVersion: kafka.strimzi.io/v1
kind: KafkaConnector
metadata:
  name: events-connector
  namespace: events-api
  labels:
    strimzi.io/cluster: events-connect
spec:
  class: io.debezium.connector.postgresql.PostgresConnector
  tasksMax: 1
  config:
    database.hostname: events-api-db.choe4u6ye3yf.eu-central-1.rds.amazonaws.com
    database.port: "5432"
    database.user: events
    database.password: "${env:DEBEZIUM_DB_PASSWORD}"
    database.dbname: events
    database.sslmode: require
    topic.prefix: cdc
    table.include.list: public.events
    plugin.name: pgoutput
    slot.name: debezium_events
    publication.name: dbz_events_publication
    publication.autocreate.mode: filtered
    key.converter: org.apache.kafka.connect.json.JsonConverter
    key.converter.schemas.enable: "false"
    value.converter: org.apache.kafka.connect.json.JsonConverter
    value.converter.schemas.enable: "false"
```

Same `publication.autocreate.mode: filtered` as the kind version — still
using the pre-created publication from Task 8, not Debezium's own
autocreate (same Debezium 3.0.0.Final bug already documented in
`k8s/overlays/cdc/scripts/create-publications.sql`'s own comment).

- [ ] **Step 2: Apply**

```bash
kubectl apply -f k8s/overlays/aws-cdc/kafka-connectors.yaml
```

- [ ] **Step 3: Verify**

```bash
kubectl -n events-api get kafkaconnector
```

Expected: both show `READY` = `True`.

```bash
kubectl -n events-api get kafkatopic
```

Expected: `cdc.public.tenant_accounts` and `cdc.public.events` (auto-created
by the broker on first message, may take a moment after the initial
snapshot).

---

### Task 10: MongoDB Community Operator via Helm

**Files:** none new — Helm-managed, no values override needed beyond defaults.

**Interfaces:**
- Produces: the `mongodbcommunity.mongodb.com/v1` CRDs and the operator watching `events-api`, needed by Task 11's `MongoDBCommunity` CR.

- [ ] **Step 1: Add the Helm repo and install**

```bash
helm repo add mongodb https://mongodb.github.io/helm-charts
helm repo update

helm install community-operator mongodb/community-operator \
  --version 0.13.0 \
  --namespace events-api \
  --set operator.watchNamespace=events-api
```

- [ ] **Step 2: Verify**

```bash
kubectl -n events-api get pods -l app.kubernetes.io/name=community-operator-mongodb-kubernetes-operator
```

Expected: one pod, `Running`, `1/1 Ready`.

```bash
kubectl get crd mongodbcommunity.mongodbcommunity.mongodb.com
```

Expected: exists.

---

### Task 11: `MongoDBCommunity` CR

**Files:**
- Create: `k8s/overlays/aws-cdc/mongodb-community.yaml`

**Interfaces:**
- Consumes: the `gp3` StorageClass (Task 3), the Community Operator (Task 10).
- Produces: a Secret `events-mongo-admin-cdc-consumer` (Community
  Operator's naming convention: `<resource-name>-<user-name>-<user-db>`)
  containing `connectionString.standard` — Task 12's consumer Deployment
  reads the password out of this to build `STREAMING_MONGO_URI`.

- [ ] **Step 1: Create the seed-password Secret (imperative, not committed)**

```bash
MONGO_USER_PW=$(openssl rand -base64 24)

kubectl create secret generic mongo-consumer-seed-password \
  --namespace events-api \
  --from-literal=password="$MONGO_USER_PW"
```

- [ ] **Step 2: Write the CR**

Grounded against the Community Operator's own example CRs (`config/samples/
mongodb.com_v1_mongodbcommunity_cr.yaml` and `arbitrary_statefulset_
configuration/mongodb.com_v1_custom_volume_cr.yaml` in the `mongodb/
mongodb-kubernetes-operator` repo at tag `v0.13.0`) for the exact
`volumeClaimTemplates` name (`data-volume`) and `users`/`security` shape.

```yaml
apiVersion: mongodbcommunity.mongodb.com/v1
kind: MongoDBCommunity
metadata:
  name: events-mongo
  namespace: events-api
spec:
  members: 1
  type: ReplicaSet
  version: "7.0.0"
  security:
    authentication:
      modes: ["SCRAM"]
  users:
    - name: cdc-consumer
      db: admin
      passwordSecretRef:
        name: mongo-consumer-seed-password
      roles:
        - name: readWrite
          db: events_projection
      scramCredentialsSecretName: cdc-consumer-scram
  statefulSet:
    spec:
      volumeClaimTemplates:
        - metadata:
            name: data-volume
          spec:
            accessModes: ["ReadWriteOnce"]
            storageClassName: gp3
            resources:
              requests:
                storage: 2Gi
```

`members: 1` — deliberately not the chart's usual 3, per this session's
capacity finding in `AWS_PLAN.md`. This is a real, accepted trade-off: a
single-member "replica set" has no actual HA, same class of trade-off as
this project's single-NAT-gateway decision in Milestone 1.

- [ ] **Step 3: Apply**

```bash
kubectl apply -f k8s/overlays/aws-cdc/mongodb-community.yaml
```

- [ ] **Step 4: Verify**

```bash
kubectl -n events-api get mongodbcommunity events-mongo -w
```

Expected: `PHASE` becomes `Running`.

```bash
kubectl -n events-api get secret events-mongo-admin-cdc-consumer -o jsonpath='{.data.connectionString\.standard}' | base64 -d
```

Expected: a `mongodb://cdc-consumer:...@events-mongo-0.events-mongo-svc.events-api.svc.cluster.local:27017/admin?replicaSet=events-mongo&...` -shaped
URI. **Confirm the actual Secret name and key here empirically** — Community
Operator's exact auto-generated Secret naming has varied across versions
historically; if this jsonpath 404s, `kubectl -n events-api get secrets |
grep events-mongo` to find the real name before hardcoding it into Task 12.

---

### Task 12: Consumer wiring + the `aws-cdc` overlay itself

**Files:**
- Create: `k8s/overlays/aws-cdc/consumer-deployment.yaml`
- Create: `k8s/overlays/aws-cdc/kustomization.yaml`
- Modify: `k8s/overlays/aws-cdc/kafka-connect.yaml` (substitute the real ECR URL from Task 7's `REPLACE_WITH_ECR_URL` placeholder)

**Interfaces:**
- Consumes: `events-kafka-bootstrap:9092` (Task 5), the Mongo connection Secret (Task 11), `events-api-secrets` (existing, from `k8s/base` — still needed for `APP_DATABASE_URL` if the consumer ever reads Postgres directly; verify it actually needs this before including it, since the consumer only reads Kafka/writes Mongo).

- [ ] **Step 1: Substitute the real ECR URL into the `KafkaConnect` CR**

```bash
ECR_URL=$(cd terraform && AWS_PROFILE=events-api-tf terraform output -json ecr_repository_urls | jq -r '."kafka-connect"')
sed -i '' "s|REPLACE_WITH_ECR_URL|$ECR_URL|" k8s/overlays/aws-cdc/kafka-connect.yaml
```

(Same "real AWS identifiers get checked in literally" convention as
`k8s/overlays/aws/deployment-patch.yaml`'s hardcoded image URL — this
account/region don't change between applies, so this is a one-time
substitution, not a templating mechanism to build.)

- [ ] **Step 2: Extract the Mongo password for the consumer's env**

```bash
MONGO_URI=$(kubectl -n events-api get secret events-mongo-admin-cdc-consumer \
  -o jsonpath='{.data.connectionString\.standard}' | base64 -d)

kubectl create secret generic streaming-mongo-credentials \
  --namespace events-api \
  --from-literal=STREAMING_MONGO_URI="$MONGO_URI"
```

(Real Secret name/key confirmed per Task 11's Step 4 caveat before running
this.)

- [ ] **Step 3: Write the consumer Deployment**

Same shape as `k8s/overlays/cdc/cdc-consumer-deployment.yaml`, repointed:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cdc-consumer
  namespace: events-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cdc-consumer
  template:
    metadata:
      labels:
        app: cdc-consumer
    spec:
      containers:
        - name: cdc-consumer
          image: REPLACE_WITH_STREAMING_ECR_URL:latest
          imagePullPolicy: Always
          env:
            - name: STREAMING_KAFKA_BOOTSTRAP_SERVERS
              value: "events-kafka-bootstrap:9092"
          envFrom:
            - secretRef:
                name: streaming-mongo-credentials
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
```

Substitute the real streaming image URL the same way as Step 1:

```bash
STREAMING_ECR_URL=$(cd terraform && AWS_PROFILE=events-api-tf terraform output -json ecr_repository_urls | jq -r '.streaming')
sed -i '' "s|REPLACE_WITH_STREAMING_ECR_URL|$STREAMING_ECR_URL|" k8s/overlays/aws-cdc/consumer-deployment.yaml
```

(If the `events-api-streaming` image was never actually built/pushed to
this ECR repo in an earlier milestone, build and push it now the same way
as Task 6 Step 3, using `docker build --target runtime-streaming` per this
repo's root `Dockerfile` — check `docker images`/ECR first before assuming
it's missing.)

- [ ] **Step 4: Write the overlay's `kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../aws
  - storage-class.yaml
  - kafka-cluster.yaml
  - kafka-connect.yaml
  - kafka-connectors.yaml
  - mongodb-community.yaml
  - consumer-deployment.yaml
  - create-publications-job.yaml

configMapGenerator:
  - name: aws-cdc-publications-sql
    namespace: events-api
    files:
      - scripts/create-publications.sql
```

- [ ] **Step 5: Dry-run before touching the cluster** (this repo's own established Milestone 2 precedent — dry-run caught 4 real bugs there before they hit the cluster)

```bash
kubectl kustomize k8s/overlays/aws-cdc
```

Read through the full render — confirm no `no matches for Id` errors and
that every manifest applied ad hoc in earlier tasks (Kafka cluster, Connect,
connectors, Mongo, StorageClass) now also appears here, so `kubectl apply -k`
is idempotent against what's already live.

- [ ] **Step 6: Apply the full overlay**

```bash
kubectl apply -k k8s/overlays/aws-cdc
```

- [ ] **Step 7: Verify the publications Job and consumer both come up clean**

```bash
kubectl -n events-api get job aws-cdc-create-publications
kubectl -n events-api logs job/aws-cdc-create-publications
kubectl -n events-api get pods -l app=cdc-consumer
kubectl -n events-api logs -l app=cdc-consumer --tail=50
```

Expected: Job `Complete`; consumer pod `Running`, logs show it subscribed
to both topics with no connection errors.

---

### Task 13: End-to-end verification + document the outcome

**Files:**
- Modify: `WHATS_NEXT.md` (append a new entry under the AWS_PLAN.md Milestone
  section, matching this project's established "Verified live..." convention
  — only after the checks below actually pass, recording what was actually
  verified, not what was intended).

**Interfaces:** none — this task only verifies the wiring from Tasks 1-12.

- [ ] **Step 1: Confirm every piece is healthy before generating load**

```bash
kubectl -n events-api get kafka,kafkanodepools,kafkaconnect,kafkaconnector,mongodbcommunity
```

Expected: everything `READY`/`Running`/`True`.

- [ ] **Step 2: Post a real event through the app**

```bash
kubectl -n events-api port-forward svc/events-api 8000:8000 &
PF_PID=$!

TENANT_ID=$(curl -s -X POST localhost:8000/admin/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "aws-milestone-3-verify", "plan_tier": "free"}' | jq -r .id)

curl -s -X POST localhost:8000/events \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -d '{"event_type": "verify.milestone3", "occurred_at": "2026-09-04T12:00:00Z", "properties": {"proof": "aws-cdc-round-trip"}}'

kill $PF_PID
```

- [ ] **Step 3: Confirm it reached Mongo**

```bash
kubectl run -n events-api mongo-check --rm -it --restart=Never \
  --image mongo:7 -- \
  mongosh "$MONGO_URI" --eval 'db.event_properties.find({"properties.proof": "aws-cdc-round-trip"}).pretty()'
```

Expected: one document matching the posted event, `properties.proof` equal
to `"aws-cdc-round-trip"` — the same round-trip proof this project already
established on kind, now proven against real RDS/Strimzi/MongoDB Community
Operator infrastructure.

- [ ] **Step 4: Update `WHATS_NEXT.md`**

Append a new bullet under the AWS_PLAN.md Milestones section, in this
project's established style (what was built, what broke and how it was
fixed, what was verified live and how) — write this only once Steps 1-3
have actually passed, since this file's whole purpose is recording verified
state, not intentions.

- [ ] **Step 5: Remind about teardown**

This milestone adds Kafka/Connect/Mongo pods + `gp3` EBS volumes + a second
node on top of the already-running EKS/RDS/NAT stack. Per `AWS_PLAN.md`'s
own cost discipline, flag to the user that ending the session means either
tearing this down or consciously accepting the ongoing cost — don't
auto-decide either way.

---

### Task 14 (optional polish): `Name` tag on the EKS node group's EC2 instances

**Files:**
- Modify: `terraform/modules/eks/main.tf`

**Interfaces:**
- Produces: a `Name` tag on the two node-group EC2 instances (console
  cosmetic only — nothing else in this plan reads or depends on it).

Found during Task 2's console check: the node group's instances show a blank
`Name` field in the EC2 console. `kubectl get nodes`' `NAME` column is
unaffected either way — that comes from the instance's AWS-assigned private
DNS hostname, a mechanism entirely separate from the EC2 "Name" tag. Verified
against the actual `aws_eks_node_group` Terraform docs before writing this:
its own `tags` argument does **not** propagate to the underlying EC2
instances (only EKS/ASG-internal tags do, which is exactly what `aws ec2
describe-instances` showed — `eks:nodegroup-name`, `aws:autoscaling:groupName`,
etc., no `Name`). The documented fix for instance-level tags is a
`launch_template` with its own `tag_specifications` block.

- [ ] **Step 1: Add a minimal, tags-only launch template**

In `terraform/modules/eks/main.tf`, add before `aws_eks_node_group "this"`:

```hcl
resource "aws_launch_template" "node" {
  name_prefix = "${var.name_prefix}-node-"

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.name_prefix}-node"
    }
  }
}
```

Deliberately minimal — no `instance_type`/`image_id` set here, so the node
group's existing `instance_types = ["t4g.small"]` and
`ami_type = "AL2023_ARM_64_STANDARD"` arguments keep controlling those (this
is the standard "tags-only launch template" pattern; the alternative of
moving instance type/AMI into the launch template too is unnecessary scope
for what this task is actually for).

- [ ] **Step 2: Wire it into the node group**

In the same file, add this block inside `aws_eks_node_group "this"` (order
within the resource doesn't matter):

```hcl
  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }
```

- [ ] **Step 3: Plan and review carefully**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform plan
```

Expect `1 to add` (the launch template) and `1 to change` (the node group, in-place). **If this shows a node group replacement instead of an in-place update, stop and investigate before applying** — introducing a launch template shouldn't force replacement of already-running nodes, but this is exactly the kind of "looks like a one-liner, actually forces a full rebuild" trap this project has hit before (Milestone 2's `storage_encrypted` change); confirm the plan says update, not destroy/recreate.

- [ ] **Step 4: Apply**

```bash
AWS_PROFILE=events-api-tf terraform apply
```

- [ ] **Step 5: Verify**

```bash
aws ec2 describe-instances --profile events-api-tf --region eu-central-1 \
  --filters "Name=tag:eks:nodegroup-name,Values=events-api-default" \
  --query 'Reservations[].Instances[].Tags[?Key==`Name`]' --output json
```

Expect both instances to show a `Name` tag now. Note this only affects *new*
instances launched after the change (a running node group won't retroactively
tag already-running instances just because the launch template changed) — if
the existing two nodes still show blank after this, that's expected, not a
bug; a future node replacement (scaling event, AMI upgrade) would pick up the
tag then. Confirm nothing else regressed too:

```bash
kubectl get nodes
```

Expect the same 2 nodes, still `Ready`, same architecture (arm64) — this
change should be invisible from Kubernetes' own perspective.

---

## Notes for whoever executes this

- Tasks 1-2 (Terraform) are real, billable, hard-to-reverse changes against
  live AWS infrastructure — per this repo's `CLAUDE.md`, present the diff and
  get explicit go-ahead before `terraform apply`, same as every other
  milestone so far.
- Two things this plan flags as "verify empirically, don't assume" rather
  than asserting as fact: whether the RDS master user already carries
  `rds_replication` (Task 8, Step 4) and the Community Operator's exact
  auto-generated Secret name/key for the Mongo connection string (Task 11,
  Step 4) — Strimzi and MongoDB Operator conventions were checked directly
  against their 1.2.0/v0.13.0 sources during planning, but these two specific
  facts depend on live cluster/account state this plan can't observe in
  advance.
- Task 14 is optional polish, not a dependency of anything else in this
  plan — safe to skip entirely, or do at the very end, or skip permanently.
  If skipped, the existing two nodes simply keep showing a blank `Name` in
  the EC2 console indefinitely; nothing else is affected.
