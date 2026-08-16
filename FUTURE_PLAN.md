# Future Plan: Closing Job-Posting Gaps (Milestones 10+)

## Context

`PLAN.md`'s Milestones 0–9 (the original local-first FastAPI + dbt + k8s + Terraform scope) are complete. This document picks up from there, kept as a genuinely separate file rather than appended to `PLAN.md` — `PLAN.md`'s own Context section already establishes that convention for a distinct axis of future work ("A real-AWS/EKS deployment is a deliberately separate follow-up plan, not part of this one"); this is the same idea applied to a second, different follow-up.

Origin: rechecking the full text of the target job posting (Beamery, Senior Data Engineer) line by line — not just the headline stack list — against what Milestones 0–9 actually cover. Two lists matter:

**Previous professional experience** (the posting's actual requirements):
- Data transformations using dbt, including patterns for large, complex projects
- Data storage (SQL / NoSQL): schema design, modelling at scale, and multi-tenant isolation
- Back-end engineering: Python (nice to have: TypeScript/Node.js)
- Pipelines: streaming, CDC, correctness, replayability, and evolution over time
- Data quality and observability: validation, testing, data contracts, monitoring, alerting, and incident response
- Infrastructure as code and containerisation (Terraform, Kubernetes)

**Their stack** (what they actually use — the posting is explicit that tool-specific experience matters less than the ability to learn):
dbt, BigQuery, Kafka, PostgreSQL & MongoDB, TypeScript/Node.js, Kubernetes, Python, Segment.

Gap check against Milestones 0–9: SQL schema/multi-tenant isolation (Milestone 7), IaC/k8s (Milestones 4–6), Python backend, dbt basics, streaming/CDC mechanics (Milestone 8), and data quality/alerting (Milestone 9) are all covered. What's covered by name but not by the *specific properties* the posting calls out (correctness/replayability/schema evolution on pipelines; data contracts specifically; "large, complex" dbt patterns), and what's a total gap (BigQuery, MongoDB) is the actual content of this document. TypeScript/Node and Segment were checked and deliberately skipped — see the bottom of this file, not silently ignored.

## Milestones

### 10. Pipeline correctness, replayability, and schema evolution

"Streaming, CDC, **correctness, replayability, and evolution over time**" is its own line in the posting. Milestone 8 proved the CDC mechanism exists; it never exercised these three specific properties, which are exactly what an interviewer probes on a CDC claim. No new infrastructure — reuses the Kafka/Debezium stack Milestone 8 already built.

- **Replay**: reset `streaming/consumer.py`'s consumer group to an earlier offset and reprocess already-seen change events, proving replay is actually possible on this stack, not just theoretically true of Kafka's log retention.
- **Idempotency under replay**: extend the consumer to write into a real Postgres projection (an audit-log table), keyed so reprocessing the same change event twice doesn't duplicate — upsert on a stable key, not a blind insert. This is what turns "I logged the events" (Milestone 8) into "I handled replay correctly" (this milestone).
- **Schema evolution**: add a column to `tenant_accounts` via a new Alembic migration while the connector is running, and observe exactly what Debezium emits for the next change event — does the new column just appear in `after`? Does anything downstream choke on an unexpected key? Write down what actually happens; this is the "evolution over time" question a real CDC pipeline has to survive, not a hypothetical.
- Deliberately not building a schema registry (Avro/Confluent Schema Registry, compatibility rules) — this stack uses plain `JsonConverter`, so schema evolution here means "what happens with loosely-typed JSON," a genuinely different and lighter problem than registry-enforced compatibility, which is its own separate piece of infrastructure.

### 11. dbt at scale: incremental models, layering, and contracts

Covers two named requirements at once — "dbt... patterns for large, complex projects" and "data contracts" (under data quality) — since both are really about dbt model-design discipline as a project grows past two models. Current `dbt/` project (a view and a full-rebuild table) is fine for its size but doesn't demonstrate any pattern a genuinely large dbt project needs.

- Convert (or add) a mart to `materialized: incremental` — merge/insert only new rows on each run instead of recomputing the whole table, via an `is_incremental()` guard and a `unique_key`. Forces a real decision about late-arriving events, worth writing down rather than hand-waving.
- Add an intermediate layer (`dbt/models/intermediate/`) between staging and marts, even a small one — demonstrates the staging → intermediate → marts convention that exists specifically for when more than one mart needs the same cleaned-up shape.
- Enable a dbt **contract** (`+contract: {enforced: true}` plus explicit column types/constraints in `schema.yml`) on a mart — dbt then refuses to build if the query's actual output doesn't match the declared contract, catching schema drift at build time instead of it silently reaching consumers.
- A dbt **snapshot** (`dbt/snapshots/`) on `tenant_accounts` — SCD Type 2 change tracking (`dbt_valid_from`/`dbt_valid_to`) as the dbt-native alternative to the Debezium CDC stream on the same table; worth knowing both exist and when each is the right tool.
- Deliberately not building: a dbt package published to the hub, a semantic layer/MetricFlow, or CI-integrated state-aware ("slim CI") runs — the last one needs a real CI pipeline, already out of scope per `PLAN.md`'s Scope discipline.

### 12. BigQuery as a second dbt target

Covers "BigQuery data warehouse" — a total gap otherwise; this project has only ever run dbt against Postgres. dbt's actual selling point (SQL that's largely adapter-portable) is best proven by swapping the adapter for real, not read about.

- New `dbt-bigquery` optional extra (same pattern as the existing `dbt`/`streaming` extras in `pyproject.toml`), a `bigquery` target added to `dbt/profiles.yml` alongside the existing `postgres` one.
- Needs a real GCP project with a **billing account attached**, not BigQuery's no-billing Sandbox mode — Sandbox blocks all DML, which breaks the incremental model from Milestone 11. Set a budget alert as a safety net; BigQuery's always-free monthly allowance (10 GB storage, 1 TB queries, confirmed current as of this document's writing) applies to any project regardless of Sandbox/billing status, so a learning-scale project stays well within it — same $0 framing as `PLAN.md` Milestone 5's LocalStack usage.
- Point `stg_events`/a mart at the BigQuery target via `--target bigquery`, adjusting for real dialect differences: BigQuery has no session-scoped RLS equivalent — multi-tenant isolation there is authorized views or row-level access policies, a genuinely different mechanism worth comparing directly against `PLAN.md` Milestone 7's Postgres RLS — plus `partition_by` (on a date/timestamp column) and `cluster_by` (on `tenant_id`) in `dbt_project.yml`, BigQuery-specific cost/performance levers Postgres has no equivalent of.
- Deliberately not migrating the primary pipeline off Postgres — this proves portability with a second target, not a replacement.

### 13. MongoDB

The only NoSQL gap — named explicitly alongside PostgreSQL in both the experience and stack lists.

- One genuine use case rather than bolting Mongo on for its own sake: `events.properties` is already an unstructured JSONB blob with no fixed schema (`stg_events.sql`'s own comment already calls it out as "intentionally unstructured/unvalidated") — a legitimate candidate for actually living in Mongo instead of Postgres JSONB, since that's precisely the variable-shape document data Mongo is for.
- A deliberate document-shape decision (embed vs. reference, given `properties` is read far more than written here), written down as a real modeling choice — "schemaless" doesn't mean "no design."
- Deliberately not building a dual-write consistency story between Postgres and Mongo — that's a distributed-transactions problem bigger than this milestone. Simplest credible version: a one-directional projection (properties mirrored into Mongo), not two systems of record for the same data.

## Explicitly skipped, not deferred

Checked against the posting and decided against, rather than left unconsidered:

- **TypeScript/Node.js backend** — posting lists Python as primary, TS/Node as "nice to have." This project already demonstrates strong Python backend work; a parallel Node slice would prove familiarity with a second language, not close a skill gap, and the posting's own framing ("ability to learn new tools matters more than experience with any specific one") explicitly de-prioritizes exactly this kind of tool-specific coverage.
- **Segment** — a vendor customer-data-platform product. `POST /events` is already conceptually the thing Segment replaces (an event-collection endpoint); standing up an actual Segment account to prove "I can use a SaaS dashboard" doesn't demonstrate a skill this project doesn't already cover, and the posting's "learn new tools" framing applies here most directly of anything on this list.
- **kind/k8s deployment of the Kafka/Debezium stack** — already deferred by `PLAN.md` Milestone 8's own scoping note (Kafka-on-Kubernetes is a real, separate skill); repeated here only so it isn't lost between two documents.

## Production concepts this teaches (mapped to milestones)

| Concept | Milestone |
|---|---|
| CDC replay, idempotent projections, schema evolution | 10 |
| Incremental dbt models, model layering, dbt contracts, snapshots | 11 |
| Cross-warehouse dbt portability (BigQuery), partitioning/clustering | 12 |
| NoSQL document modeling alongside a relational store | 13 |

## Verification

- Milestone 10: a consumer group offset reset causes already-seen change events to reprocess without duplicating the audit-log projection; adding a column to `tenant_accounts` mid-stream produces a change event whose shape is documented, not assumed
- Milestone 11: `dbt run` on the incremental mart only touches new rows (confirmed via row counts / query plan, not just "it didn't error"); a deliberately-mismatched column type fails the build via the dbt contract instead of silently succeeding; `dbt snapshot` produces a second row with `dbt_valid_from`/`dbt_valid_to` set correctly after a source update
- Milestone 12: `dbt run --target bigquery` succeeds against a real BigQuery project within the free tier; partition/cluster config confirmed via BigQuery's own query plan (partition pruning visible, not just configured); a manual cross-tenant query against the chosen isolation mechanism (authorized view / row-level access policy) confirms it actually restricts rows, mirroring Milestone 7's RLS proof
- Milestone 13: a round-trip through the Mongo projection (write to Postgres → observable in Mongo) works end-to-end; the chosen document shape documented with the embed-vs-reference reasoning behind it
