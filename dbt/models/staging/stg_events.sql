-- Staging: clean/cast passthrough of the app's raw `events` table.
-- Materialized as a view via dbt_project.yml's staging default — no need
-- to repeat that config here.
--
-- Your turn: select every column from the source below, casting each one
-- explicitly (even where the cast is a no-op). That's the dbt convention
-- for staging models — make the type contract explicit rather than
-- relying on whatever the source happens to be today, so a source schema
-- drift breaks loudly here instead of silently downstream.
--
-- Columns: id, tenant_id, event_type, user_id, occurred_at, ingested_at, properties.
-- Nothing needs to unpack `properties` yet, so pass the JSONB through as-is
-- rather than extracting fields nobody's asked for (YAGNI applies to
-- transformations too, not just application code).
--
-- Milestone 7: add tenant_id here (same explicit-cast convention as every
-- other column - it's already uuid at the source, cast it anyway). This is
-- the one line that makes tenant_id available to daily_event_counts below.

select
    id::uuid as id,
    tenant_id::uuid as tenant_id,
    event_type::varchar as event_type,
    user_id::varchar as user_id,
    occurred_at::timestamptz as occurred_at,
    ingested_at::timestamptz as ingested_at,
    properties::jsonb as properties
from {{ source('app', 'events') }}
