-- Milestone 12: multi-tenant isolation on BigQuery, compared directly
-- against PLAN.md Milestone 7's Postgres RLS. Not built via dbt (BigQuery
-- row access policies are DDL applied once, outside any model's build
-- cycle) — applied manually against this table:
--
--   CREATE ROW ACCESS POLICY tenant_a_only
--   ON events_analytics.bq_daily_event_counts
--   GRANT TO ('serviceAccount:big-query@project-e8569bd6-524d-42fe-bb9.iam.gserviceaccount.com')
--   FILTER USING (tenant_id = 'tenant-a')
--
-- Confirmed empirically 2026-08-22, querying as two distinct principals:
-- the granted service account saw only tenant-a (as expected), but
-- querying as the project Owner (not named in any GRANT TO) returned
-- ZERO rows, not everything. This is the opposite of Postgres RLS's
-- behavior (d7a67740cfa5's own migration docstring: "superusers bypass
-- RLS unconditionally, regardless of ownership or FORCE ROW LEVEL
-- SECURITY") — BigQuery has no owner-bypass exception for row access
-- policies at all; every principal not explicitly granted is fail-closed,
-- full stop. Seeing everything would require the specific
-- bigquery.rowAccessPolicies.overrideRestriction permission, which Owner
-- doesn't carry by default. Genuinely different security model, not just
-- different syntax for the same idea.

{{
    config(
        partition_by={'field': 'utc_date', 'data_type': 'date'},
        cluster_by=['tenant_id']
    )
}}

with generated_events as (
    select
        date_sub(current_date(), interval mod(row_number, 40) day) as utc_date,
        case mod(row_number, 3)
            when 0 then 'tenant-a'
            when 1 then 'tenant-b'
            else 'tenant-c'
        end as tenant_id,
        case mod(row_number, 2)
            when 0 then 'page_view'
            else 'click'
        end as event_type
    from unnest(generate_array(1, 2000)) as row_number
)

select
    tenant_id,
    event_type,
    utc_date,
    count(*) as event_count
from generated_events
group by tenant_id, event_type, utc_date
