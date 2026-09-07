# AWS Milestone 5 (BigQuery Sink via IRSA/GCP WIF) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` establishes hands-on
> teaching mode as the default for every new milestone — explain what changes
> and why, hand the user the exact command/file content, let them run
> Bash/Write/Edit themselves, then verify by reading the result back. That
> convention takes precedence over either sub-skill's default of an agent
> autonomously executing steps, until the user explicitly hands over execution
> for this stretch of work.

**Goal:** Add a BigQuery sink connector to the existing Strimzi Kafka Connect
instance, authenticated via IRSA chained with GCP Workload Identity
Federation (no static GCP key anywhere), streaming `cdc.public.events`/
`cdc.public.tenant_accounts` into real BigQuery tables — then build a new dbt
model on top of that real data, giving `AWS_PLAN.md`'s "Kafka for streaming"
and "BigQuery warehouse" requirements one coherent, real proof instead of two
disconnected checkbox items.

**Architecture:** One new AWS-side IRSA role (zero permissions attached — its
only job is proving AWS identity to GCP's WIF handshake). GCP-side setup is
manual `gcloud` (Workload Identity Pool + AWS provider + impersonation
binding), matching Milestone 12's own precedent of keeping GCP resources out
of this repo's AWS-only Terraform. The existing custom Kafka Connect image
(from AWS Milestone 3) gets a second connector plugin added (Aiven's BigQuery
sink). Two new `KafkaConnector` sink resources feed real BigQuery tables; a
new dbt model reads them.

**Tech Stack:** Aiven's `bigquery-connector-for-apache-kafka` (Apache 2.0,
class `com.wepay.kafka.connect.bigquery.BigQuerySinkConnector`), GCP
Workload Identity Federation (AWS provider), Strimzi 1.2.0 (already running),
dbt.

**Spec:** `AWS_PLAN.md`'s "5. BigQuery as a real warehouse, fed by the same
CDC pipeline" section (updated 2026-09-06 with this session's findings) —
this plan argues from that section; read it alongside this plan.

## Global Constraints

- GCP project is `project-e8569bd6-524d-42fe-bb9`, **project number
  `10216729029`** — the number, not the ID string, is required for the WIF
  impersonation binding specifically. Getting this wrong produces a
  confusing "principal not found"-style failure, not an obviously-named
  error, so double check which one a given `gcloud` flag actually wants.
- AWS account ID is `938500344309` (needed for the WIF AWS provider's
  `--account-id` flag).
- Existing live resources this plan builds on top of, confirmed via direct
  `gcloud`/`bq` calls this session, not assumed: service account
  `big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com`
  (already used by Milestone 12's dbt BigQuery target), dataset
  `events_analytics`.
- Kafka Connect's real, already-existing ServiceAccount is
  `events-connect-connect` in namespace `events-api` — confirmed live via
  `kubectl get serviceaccounts`. Any IRSA trust policy referencing it must
  use exactly `system:serviceaccount:events-api:events-connect-connect`.
- `AWS_PROFILE=events-api-tf` for all AWS CLI calls; no equivalent env var
  for `gcloud` — that already operates against
  `project-e8569bd6-524d-42fe-bb9` per the active `gcloud config`.
- BigQuery-facing SQL/metadata operations (table creation, verification
  queries) use the standard `bq` CLI, same as Milestone 12 — **deliberately
  not** a Claude-specific tool, even where one happens to be available in
  this environment. This project is a portfolio artifact meant to
  demonstrate real, standard tool usage; a step that only works inside
  Claude Code's own plugin ecosystem isn't something anyone else could
  reproduce, which defeats the point.
- Nothing containing real secret material gets committed — the WIF
  credential-config JSON is the one exception explicitly confirmed safe to
  commit (it contains routing/mapping configuration, not key material), but
  confirm that's still true for whatever the actual generated file looks
  like before committing it, rather than assuming the general claim holds
  for every field.
- Two things this plan explicitly flags as "verify empirically, don't
  assume" — checked as far as documentation allows during planning, but
  dependent on the actual running connector: (1) whether Aiven's fork
  publishes a ready-to-use plugin archive or requires building from source,
  (2) whether Aiven's added `credential_source` allowlisting restrictions
  apply to AWS-sourced WIF credentials specifically (they use a different,
  first-party code path than the file/URL/executable sources those
  restrictions target, but this needs confirming against the connector
  actually running, not just its documented design).

---

## File Structure

**Terraform (modified):**
- `terraform/modules/iam/main.tf` — add a new IRSA role for Kafka Connect's
  GCP-federation identity (co-located with the app's existing IRSA role,
  since this module's whole purpose is already "AWS identities this project
  needs").
- `terraform/modules/iam/variables.tf` / `outputs.tf` — if needed for wiring
  the new role's ARN out to root `main.tf`/`outputs.tf`, following the exact
  pattern `app_irsa_role_arn` already uses.
- `terraform/main.tf`, `terraform/outputs.tf` — wire the new role through,
  same pattern as the existing IRSA output.

**New (local, not committed — GCP setup artifacts):**
- A working directory for the `gcloud`-generated credential-config JSON
  before it becomes a ConfigMap (e.g. `/tmp` or the scratchpad — this file
  itself is fine to commit per the constraint above, but generate it fresh
  each time rather than hand-editing a committed copy, since it embeds the
  real AWS account ID/role ARN and GCP project number).

**Modified: `k8s/overlays/aws-cdc/kafka-connect.yaml`:**
- Add `spec.template.serviceAccount.metadata.annotations` (the IRSA role
  ARN) and the credential-config file wiring (env var + volume mount).

**New: `k8s/overlays/aws-cdc/gcp-wif-credential-config.yaml`** (or similar) —
the ConfigMap holding the generated credential-config JSON.

**Modified: `kafka-connect/Dockerfile`** — add the Aiven BigQuery sink
connector's plugin files to `/opt/kafka/plugins/`, same directory Debezium's
plugin already lives in but its own subdirectory (Strimzi loads every
subdirectory under `/opt/kafka/plugins/` independently).

**New: `k8s/overlays/aws-cdc/bigquery-connectors.yaml`** — two new
`KafkaConnector` sink resources.

**New: `dbt/models/bigquery/bq_events_from_cdc.sql`** (name illustrative,
finalize during Task 10) plus its `schema.yml` entry — a real mart built
from the sink-connector-populated tables, under the same `bigquery:` key in
`dbt_project.yml` Milestone 12 already established (`+enabled: "{{
target.name == 'bigquery' }}"` guard already exists there and applies to
this new model too).

---

### Task 1: Dedicated IRSA role for Kafka Connect (Terraform)

**Files:**
- Modify: `terraform/modules/iam/main.tf`
- Modify: `terraform/modules/iam/variables.tf` (if the module doesn't already
  have every variable this needs — check before assuming a new one is
  required)
- Modify: `terraform/main.tf`, `terraform/outputs.tf`

**Interfaces:**
- Consumes: `var.oidc_provider_arn`/`var.oidc_provider_url` (already passed
  into this module for the existing `app_irsa` role — reuse, don't
  duplicate).
- Produces: `aws_iam_role.kafka_connect_gcp_irsa` (name illustrative), whose
  ARN Task 2 annotates onto Kafka Connect's ServiceAccount.

- [ ] **Step 1: Add the trust policy and role**

In `terraform/modules/iam/main.tf`, add a second IRSA trust-policy data
source and role, mirroring the existing `irsa_trust`/`app_irsa` pair but
scoped to Kafka Connect's real ServiceAccount:

```hcl
data "aws_iam_policy_document" "kafka_connect_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:events-api:events-connect-connect"]
    }

    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kafka_connect_gcp_irsa" {
  name               = "${var.name_prefix}-kafka-connect-gcp-irsa"
  assume_role_policy = data.aws_iam_policy_document.kafka_connect_irsa_trust.json
}
```

**No policy attachment at all** — deliberately. This role exists purely so
GCP's WIF handshake can call `sts:GetCallerIdentity` against it and verify
"this is a real AWS-authenticated caller"; it needs no AWS-side permissions
on any AWS resource.

- [ ] **Step 2: Output the role ARN**

In `terraform/modules/iam/outputs.tf`, add (mirroring the existing
`app_irsa_role_arn` output):

```hcl
output "kafka_connect_gcp_irsa_role_arn" {
  description = "IRSA role ARN for Kafka Connect's GCP Workload Identity Federation handshake"
  value       = aws_iam_role.kafka_connect_gcp_irsa.arn
}
```

In root `terraform/outputs.tf`, add:

```hcl
output "kafka_connect_gcp_irsa_role_arn" {
  description = "IRSA role ARN for Kafka Connect's GCP WIF handshake"
  value       = module.iam.kafka_connect_gcp_irsa_role_arn
}
```

- [ ] **Step 3: Plan, review, apply**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform plan
```

Expect `2 to add` (role + trust policy data source doesn't count as a
resource, so likely just `1 to add` for the role — verify against the
actual plan output rather than assuming the count), `0 to change`, `0 to
destroy`. Paste the output before applying.

```bash
AWS_PROFILE=events-api-tf terraform apply
```

- [ ] **Step 4: Get the real role ARN for the next tasks**

```bash
cd terraform && AWS_PROFILE=events-api-tf terraform output -raw kafka_connect_gcp_irsa_role_arn
```

Save this value — Task 3's GCP attribute-mapping and Task 5's ServiceAccount
annotation both need it verbatim.

---

### Task 2: GCP Workload Identity Federation setup (manual `gcloud`)

**Files:** none — this is entirely GCP-side state, not committed to this
repo (matching Milestone 12's precedent).

**Interfaces:**
- Consumes: the IRSA role ARN from Task 1, Step 4.
- Produces: a Workload Identity Pool + AWS provider, and an impersonation
  binding on `big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com`
  — Task 3 generates the actual credential-config file from this.

- [ ] **Step 1: Create the Workload Identity Pool**

```bash
gcloud iam workload-identity-pools create "kafka-connect-pool" \
  --project="project-e8569bd6-524d-42fe-bb9" \
  --location="global" \
  --display-name="Kafka Connect AWS federation"
```

- [ ] **Step 2: Create the AWS provider**

```bash
gcloud iam workload-identity-pools providers create-aws "eks-kafka-connect" \
  --project="project-e8569bd6-524d-42fe-bb9" \
  --location="global" \
  --workload-identity-pool="kafka-connect-pool" \
  --account-id="938500344309" \
  --attribute-mapping="google.subject=assertion.arn" \
  --attribute-condition="assertion.arn.startsWith('arn:aws:sts::938500344309:assumed-role/events-api-iam-kafka-connect-gcp-irsa/')"
```

The `--attribute-condition` restricts this provider to only accept
assertions from *this specific* IRSA role — without it, any AWS-authenticated
caller in this account could attempt the WIF exchange, not just this
project's Kafka Connect pod. Confirm the exact assumed-role ARN shape (`arn:
aws:sts::<account>:assumed-role/<role-name>/<session-name>`) matches what
`aws sts get-caller-identity` actually reports once Task 5's pod is running,
rather than assuming the format — session names for IRSA-assumed roles
follow a specific convention worth confirming empirically.

- [ ] **Step 3: Grant impersonation on the existing service account**

```bash
gcloud iam service-accounts add-iam-policy-binding \
  "big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com" \
  --project="project-e8569bd6-524d-42fe-bb9" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/10216729029/locations/global/workloadIdentityPools/kafka-connect-pool/attribute.aws_role/arn:aws:iam::938500344309:role/events-api-iam-kafka-connect-gcp-irsa"
```

Note **`10216729029`** (the project *number*) here, not the project ID
string used everywhere else in this plan — confirmed via `gcloud projects
describe --format="value(projectNumber)"` during planning, a real and
easy-to-get-wrong distinction. If this step errors, check that distinction
first before anything else.

- [ ] **Step 4: Verify the pool/provider exist**

```bash
gcloud iam workload-identity-pools providers describe "eks-kafka-connect" \
  --project="project-e8569bd6-524d-42fe-bb9" \
  --location="global" \
  --workload-identity-pool="kafka-connect-pool"
```

Expect `state: ACTIVE`.

---

### Task 3: Generate the credential-config JSON and mount it

**Files:**
- Create: `k8s/overlays/aws-cdc/gcp-wif-credential-config.yaml`

**Interfaces:**
- Consumes: the pool/provider from Task 2.
- Produces: a ConfigMap `gcp-wif-credential-config` — Task 5 mounts this
  into the Kafka Connect pod and points the connector's `keyfile` config at
  its mounted path.

- [ ] **Step 1: Generate the file**

```bash
gcloud iam workload-identity-pools create-cred-config \
  "projects/10216729029/locations/global/workloadIdentityPools/kafka-connect-pool/providers/eks-kafka-connect" \
  --service-account="big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com" \
  --aws \
  --output-file="/tmp/gcp-wif-credential-config.json"
```

- [ ] **Step 2: Inspect it before committing anything**

```bash
cat /tmp/gcp-wif-credential-config.json
```

Confirm it has `"type": "external_account"` and contains no field that
looks like key material (no `private_key`, no long base64 blob) — this file
is designed to be safe to share, but verify that's actually true of what
got generated here rather than trusting the general claim blindly.

- [ ] **Step 3: Wrap it in a ConfigMap manifest**

```bash
kubectl create configmap gcp-wif-credential-config \
  --namespace events-api \
  --from-file=credential-config.json=/tmp/gcp-wif-credential-config.json \
  --dry-run=client -o yaml > k8s/overlays/aws-cdc/gcp-wif-credential-config.yaml
```

`--dry-run=client -o yaml` writes the manifest to a file without touching
the cluster yet — review the generated YAML before applying it directly or
wiring it into the overlay's `kustomization.yaml` `resources` list (add it
there in Task 5).

---

### Task 4: Package the Aiven BigQuery sink connector into the Kafka Connect image

**Files:**
- Modify: `kafka-connect/Dockerfile`

**Interfaces:**
- Produces: `/opt/kafka/plugins/bigquery-sink/` containing the connector's
  jars — a sibling directory to Debezium's existing
  `/opt/kafka/plugins/debezium-connector-postgres/`.

- [ ] **Step 1: Check what Aiven actually publishes**

```bash
curl -s https://api.github.com/repos/Aiven-Open/bigquery-connector-for-apache-kafka/releases/latest | jq '.assets[] | {name, browser_download_url}'
```

This is the real fork in the road this plan flagged as unverified during
planning — two possible outcomes:

**If a ready-to-use archive exists** (a `.tar.gz`/`.zip` containing the
connector jar + its dependency jars, the standard Kafka Connect plugin
shape), add a stage like:

```dockerfile
FROM curlimages/curl:8.11.0 AS bigquery-plugin
RUN curl -sSL -o /tmp/plugin.tar.gz <the actual asset URL from Step 1> \
  && mkdir -p /plugin && tar -xzf /tmp/plugin.tar.gz -C /plugin --strip-components=1
```

**If no prebuilt archive exists**, build from source instead:

```dockerfile
FROM maven:3.9-eclipse-temurin-21 AS bigquery-plugin
RUN git clone --depth 1 --branch v2.15.0 https://github.com/Aiven-Open/bigquery-connector-for-apache-kafka.git /src
WORKDIR /src
RUN mvn package -DskipITs -DskipTests
RUN mkdir -p /plugin && find . -name "*.jar" -exec cp {} /plugin/ \;
```

Confirm the actual latest tag via Step 1's own output or `git ls-remote
--tags` rather than trusting `v2.15.0` as still current — it was the latest
found during planning (August 2024), but check again at implementation
time.

- [ ] **Step 2: Copy the plugin into the final image**

Add to the existing final stage in `kafka-connect/Dockerfile` (after the
existing Debezium `COPY`, before `USER 1001`):

```dockerfile
COPY --from=bigquery-plugin --chown=1001:0 /plugin /opt/kafka/plugins/bigquery-sink
```

- [ ] **Step 3: Build and push**

```bash
ECR_URL=$(cd terraform && AWS_PROFILE=events-api-tf terraform output -json ecr_repository_urls | jq -r '."kafka-connect"')

docker build -t "${ECR_URL}:latest" kafka-connect/
docker push "${ECR_URL}:latest"
```

- [ ] **Step 4: Verify both plugins are present**

```bash
docker run --rm --platform linux/arm64 --entrypoint sh "${ECR_URL}:latest" -c \
  "ls /opt/kafka/plugins/debezium-connector-postgres/ && echo --- && ls /opt/kafka/plugins/bigquery-sink/"
```

Expect real `.jar` files listed under both directories.

---

### Task 5: Wire the IRSA annotation and credential-config into `KafkaConnect`

**Files:**
- Modify: `k8s/overlays/aws-cdc/kafka-connect.yaml`
- Modify: `k8s/overlays/aws-cdc/kustomization.yaml` (add the ConfigMap from
  Task 3)

**Interfaces:**
- Consumes: the IRSA role ARN (Task 1), the ConfigMap (Task 3), the new
  image (Task 4).

- [ ] **Step 1: Add the ServiceAccount annotation**

In `k8s/overlays/aws-cdc/kafka-connect.yaml`, add this under `spec:` (the
role ARN is the real value from Task 1, Step 4 — don't leave it as a
placeholder in the committed file, substitute it the same way the ECR URLs
were substituted in earlier tasks):

```yaml
  template:
    serviceAccount:
      metadata:
        annotations:
          eks.amazonaws.com/role-arn: arn:aws:iam::938500344309:role/events-api-iam-kafka-connect-gcp-irsa
    connectContainer:
      env:
        - name: DEBEZIUM_DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: debezium-db-credentials
              key: password
        - name: GOOGLE_APPLICATION_CREDENTIALS
          value: /opt/gcp/credential-config.json
      volumeMounts:
        - name: gcp-wif-credential-config
          mountPath: /opt/gcp
    pod:
      volumes:
        - name: gcp-wif-credential-config
          configMap:
            name: gcp-wif-credential-config
```

This merges into the existing `spec.template` block (which already has
`connectContainer.env` for `DEBEZIUM_DB_PASSWORD` — add to it, don't
replace it) and adds `spec.template.serviceAccount` and
`spec.template.pod.volumes` as new siblings, with the mount itself under
`spec.template.connectContainer.volumeMounts`. Both field paths confirmed
directly against the live CRD via `kubectl explain` during planning — not
assumed.

- [ ] **Step 2: Add the ConfigMap to the overlay**

In `k8s/overlays/aws-cdc/kustomization.yaml`, add `gcp-wif-credential-
config.yaml` to the `resources` list.

- [ ] **Step 3: Apply and verify the ServiceAccount got annotated**

```bash
kubectl apply -f k8s/overlays/aws-cdc/gcp-wif-credential-config.yaml
kubectl apply -f k8s/overlays/aws-cdc/kafka-connect.yaml
kubectl -n events-api get serviceaccount events-connect-connect -o jsonpath='{.metadata.annotations}'
```

Expect the `eks.amazonaws.com/role-arn` annotation to show the real role
ARN. If it doesn't appear, Strimzi may not have reconciled the ServiceAccount
yet, or may require a Connect pod restart to pick up the changed
ServiceAccount identity (IRSA credentials are injected at pod-creation time,
not live-reloaded) — check `kubectl -n events-api get pods -l
strimzi.io/cluster=events-connect` for a restart before assuming something's
wrong.

- [ ] **Step 4: Verify the AWS identity chain works, isolated from GCP**

Before trusting the full WIF exchange, confirm the AWS side alone works —
same debugging-isolation instinct Milestone 2 used for RDS IAM auth (prove
IRSA injects real credentials before blaming anything downstream):

```bash
kubectl -n events-api exec events-connect-connect-0 -c kafka-connect -- \
  sh -c 'echo $AWS_ROLE_ARN; aws sts get-caller-identity 2>&1 || echo "aws CLI not present in this image — check via env vars only"'
```

The image may not have the `aws` CLI installed (it's a Kafka/Java image, not
a general-purpose one) — if so, confirming `AWS_ROLE_ARN`/
`AWS_WEB_IDENTITY_TOKEN_FILE` are set is enough evidence IRSA itself is
wired correctly; the actual STS call happens inside the GCP client library
during the real WIF exchange in Task 7, not something you need to
independently reproduce here if the env vars are present and correct.

---

### Task 6: Create the BigQuery-side sink tables (or confirm auto-create)

**Files:** none — GCP-side state.

**Interfaces:**
- Produces: two BigQuery tables in `events_analytics`, named to mirror the
  Kafka topics (e.g. `cdc_events`, `cdc_tenant_accounts` — finalize naming
  when actually configuring the connectors in Task 7, since the connector's
  own topic-to-table naming convention may dictate this rather than being a
  free choice).

- [ ] **Step 1: Auto table creation is real and confirmed, but deliberately not used**

Confirmed directly against the connector's actual Java source
(`BigQuerySinkConfig.java`, not just docs): `TABLE_CREATE_CONFIG =
"autoCreateTables"` is a real, correctly-named config property. Deliberately
not using it here anyway: this connector's auto-table-creation needs real
schema information to infer BigQuery column types from, but the source
connectors emit schemaless JSON (`schemas.enable: false`, the same
convention every JSON converter in this project already uses) — switching
to schema-carrying JSON now just to unlock this would be a bigger,
unplanned change. Manual table creation with an explicit schema is the
more predictable path.

- [ ] **Step 2: Create the tables — a committed file, not an inline command**

Same reasoning as `k8s/overlays/aws-cdc/scripts/create-publications.sql`: a
one-time setup step like this belongs in a real, committed file, not only
in shell history, in case this ever needs reproducing from scratch. Create
`dbt/scripts/create_bigquery_sink_tables.sql` (alongside `dbt/scripts/
publish_data_quality.py` — the existing precedent for non-model auxiliary
scripts living under `dbt/scripts/`):

```sql
CREATE TABLE IF NOT EXISTS events_analytics.cdc_events (
  id STRING,
  tenant_id STRING,
  event_type STRING,
  user_id STRING,
  occurred_at TIMESTAMP,
  ingested_at TIMESTAMP,
  properties STRING
);

CREATE TABLE IF NOT EXISTS events_analytics.cdc_tenant_accounts (
  id STRING,
  name STRING,
  plan_tier STRING,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);
```

`IF NOT EXISTS` makes it idempotent, same idiom `create-publications.sql`
already uses. Run it via the standard `bq` CLI — **deliberately not** any
Claude-specific tool, even one confirmed working in this environment: this
project is a portfolio artifact meant to demonstrate real, standard tool
usage reproducible by anyone, not just inside this tooling. Read the actual
committed file rather than retyping its SQL inline — `bq query`'s own
`--help` confirms it accepts a query "on command line, or passed on stdin",
and BigQuery natively supports multiple semicolon-separated statements in
one query job, so both `CREATE TABLE` statements run as a single invocation:

```bash
bq query --project_id=project-e8569bd6-524d-42fe-bb9 --use_legacy_sql=false \
  < dbt/scripts/create_bigquery_sink_tables.sql
```

`properties` as `STRING` (not a nested/JSON type) matches how this project's
own CDC payloads already carry `properties` as a JSON-encoded string
end-to-end (the same `isinstance(properties, str)` pattern `streaming/
consumer.py` already guards against for Mongo) — parse it in the dbt model
if structured access is needed, rather than fighting the connector's
serialization here.

---

### Task 7: `KafkaConnector` sink resources

**Files:**
- Create: `k8s/overlays/aws-cdc/bigquery-connectors.yaml`
- Modify: `k8s/overlays/aws-cdc/kustomization.yaml`

**Interfaces:**
- Consumes: the plugin (Task 4), the IRSA/WIF chain (Tasks 1-5), the tables
  (Task 6).
- Produces: real rows flowing into BigQuery — Task 9's actual proof point.

- [ ] **Step 1: Write both connector CRs**

```yaml
apiVersion: kafka.strimzi.io/v1
kind: KafkaConnector
metadata:
  name: bigquery-events-sink
  namespace: events-api
  labels:
    strimzi.io/cluster: events-connect
spec:
  class: com.wepay.kafka.connect.bigquery.BigQuerySinkConnector
  tasksMax: 1
  config:
    topics: cdc.public.events
    project: project-e8569bd6-524d-42fe-bb9
    defaultDataset: events_analytics
    keyfile: /opt/gcp/credential-config.json
    keySource: FILE
    sanitizeTopics: "true"
    autoCreateTables: "false"
    key.converter: org.apache.kafka.connect.json.JsonConverter
    key.converter.schemas.enable: "false"
    value.converter: org.apache.kafka.connect.json.JsonConverter
    value.converter.schemas.enable: "false"
---
apiVersion: kafka.strimzi.io/v1
kind: KafkaConnector
metadata:
  name: bigquery-tenant-accounts-sink
  namespace: events-api
  labels:
    strimzi.io/cluster: events-connect
spec:
  class: com.wepay.kafka.connect.bigquery.BigQuerySinkConnector
  tasksMax: 1
  config:
    topics: cdc.public.tenant_accounts
    project: project-e8569bd6-524d-42fe-bb9
    defaultDataset: events_analytics
    keyfile: /opt/gcp/credential-config.json
    keySource: FILE
    sanitizeTopics: "true"
    autoCreateTables: "false"
    key.converter: org.apache.kafka.connect.json.JsonConverter
    key.converter.schemas.enable: "false"
    value.converter: org.apache.kafka.connect.json.JsonConverter
    value.converter.schemas.enable: "false"
```

`autoCreateTables: "false"` matches Task 6 having created the tables by
hand — flip to `"true"` and drop Task 6's manual table creation if Task 6's
own research confirms auto-create is reliable and preferred. `keySource:
FILE` combined with `keyfile` pointing at the mounted WIF credential-config
is the actual mechanism being tested here — confirm this exact config key
name (`keySource` vs. some other name) against the connector's real config
reference before applying, same "verify, don't assume" flag as Task 6.

- [ ] **Step 2: Wire into the overlay and apply**

Add `bigquery-connectors.yaml` to `kustomization.yaml`'s `resources`, then:

```bash
kubectl apply -f k8s/overlays/aws-cdc/bigquery-connectors.yaml
kubectl -n events-api get kafkaconnector
```

Expect both new connectors, `READY = True`. If not, pull the Connect pod's
logs immediately (`kubectl -n events-api logs events-connect-connect-0
--tail=100`) rather than guessing — this is exactly the point where the two
flagged unknowns (plugin packaging, WIF credential gating) would surface as
real errors if either assumption was wrong.

---

### Task 8: End-to-end verification — real data reaches BigQuery

**Files:** none — verification only.

- [ ] **Step 1: Post a real event**

```bash
kubectl -n events-api port-forward svc/events-api 8000:8000 &
PF_PID=$!
sleep 2

curl -s -X POST localhost:8000/events \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 73b2499f-1642-4483-b1cc-d2fb9a5c7639" \
  -d '{"event_type": "verify.milestone5", "user_id": "verify-user", "properties": {"proof": "bigquery-sink"}}'

kill $PF_PID
```

- [ ] **Step 2: Confirm it landed in BigQuery**

```bash
bq query --project_id=project-e8569bd6-524d-42fe-bb9 --use_legacy_sql=false \
  "SELECT * FROM events_analytics.cdc_events WHERE event_type = 'verify.milestone5'"
```

Expect one real row, matching the posted payload — the actual proof this
milestone exists for: `POST /events` → RDS logical replication → Debezium →
Kafka → the BigQuery sink connector → a real BigQuery table, entirely via
IRSA/WIF, zero static GCP credentials anywhere in the chain.

---

### Task 9: New dbt model on the real sink data

**Files:**
- Create: `dbt/models/bigquery/bq_events_from_cdc.sql` (or a better name
  decided at implementation time — illustrative here)
- Modify: `dbt/models/bigquery/schema.yml` (or wherever Milestone 12's
  `bq_daily_event_counts` schema entry lives — add alongside it, don't
  replace it)

**Interfaces:**
- Consumes: `events_analytics.cdc_events` (Task 6/7's real table).
- Produces: a new mart proving real aggregation over real CDC-replicated
  data — deliberately not touching `bq_daily_event_counts`, which stays
  exactly as Milestone 12 left it.

- [ ] **Step 1: Write the model**

A real daily aggregation, same shape as the Postgres-side
`daily_event_counts` mart but reading real BigQuery-native data instead of
Postgres via a foreign pipeline:

```sql
{{
  config(
    materialized='table',
    partition_by={'field': 'utc_date', 'data_type': 'date'},
    cluster_by=['tenant_id', 'event_type']
  )
}}

select
  tenant_id,
  event_type,
  date(occurred_at) as utc_date,
  count(*) as event_count
from {{ source('bigquery_cdc', 'cdc_events') }}
group by tenant_id, event_type, utc_date
```

Add the `bigquery_cdc` source (pointing at `events_analytics.cdc_events`) to
whichever `_sources.yml`-equivalent this project's `bigquery:` dbt key
already uses, or a new one if Milestone 12 didn't need one (it used a
synthetic `UNNEST` generator with no real source table).

- [ ] **Step 2: Build and verify**

```bash
cd dbt && uv run --extra dbt dbt build --target bigquery --select bq_events_from_cdc
```

Then confirm the row count matches reality, not just "the build succeeded":

```bash
bq query --project_id=project-e8569bd6-524d-42fe-bb9 --use_legacy_sql=false \
  "SELECT * FROM events_analytics.bq_events_from_cdc WHERE event_type = 'verify.milestone5'"
```

Expect exactly one row (from Task 8's test event), with `event_count = 1` —
proving the mart reflects real, current CDC-replicated data, not a fixed or
synthetic count, which is the actual verification bar `AWS_PLAN.md`'s own
Verification section states for this milestone.

---

### Task 10: Document and commit

**Files:**
- Modify: `WHATS_NEXT.md`

- [ ] **Step 1: Write up what was actually built and verified**, once Tasks
  1-9 have genuinely passed — same "record verified state, not intentions"
  discipline as every other milestone entry in this file. Include whichever
  of the two flagged unknowns (plugin packaging method, WIF credential
  gating) resolved which way, since both were real open questions during
  planning that implementation will have settled one way or the other.

- [ ] **Step 2: Remind about teardown/cost** — this milestone adds no new
  EKS/RDS infrastructure (no new nodes, no new storage), but does add a
  second Kafka Connect task/connector pair and ongoing GCP API calls (WIF
  token exchanges, BigQuery streaming inserts) — worth noting real GCP-side
  cost exposure (BigQuery streaming inserts and storage aren't part of the
  AWS cost stack tracked elsewhere in this file) alongside the existing AWS
  reminder.

---

## Notes for whoever executes this

- Tasks 1 (Terraform) and 2 (GCP IAM changes) are real, hard-to-reverse
  changes against live cloud state in two different providers — present the
  diff/commands and get explicit go-ahead before running either, same as
  every other milestone.
- This plan has more open "verify at implementation time" items than
  Milestone 3's did (connector packaging, `credential_source` gating,
  `autoCreateTables`/`keySource` exact config keys) — that's a real
  reflection of how much
  of this milestone depends on a third-party connector's actual behavior
  under a cross-cloud auth mechanism neither this project nor (as far as
  this planning session found) very much public documentation has exercised
  together before. Treat Task 7's first real apply as the actual test of
  several stacked assumptions at once, and be ready to isolate which one
  failed (plugin missing → `KafkaConnector` never reaches `RUNNING`; auth
  chain broken → a specific GCP/WIF error in the Connect pod's logs; wrong
  config key → a config-validation error at connector-creation time,
  distinct from a runtime failure) rather than guessing at the first fix
  that comes to mind.
