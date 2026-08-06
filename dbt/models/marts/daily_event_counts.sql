-- Mart: event counts per day per event_type. This is what
-- GET /analytics/daily will read — materialized as a table (see
-- dbt_project.yml) since the API queries it on every request, not
-- something that should be recomputed per-request like a view would be.
--
-- Your turn: aggregate the model below by day and event_type, with a
-- count of events in each bucket. Decide how you're truncating occurred_at
-- to a day (there's more than one way — know the trade-off of whichever
-- you pick) and what to name the resulting columns.
--
-- Reference the staging model via the `ref` function (see below), never
-- `source` directly and never a hardcoded table name — that's what gives
-- dbt its dependency graph (stg_events always runs first, automatically)
-- and what makes `dbt docs generate` render the lineage correctly.
--
-- Note for later: dbt's Jinja renderer processes this whole file,
-- including comments, before any SQL parsing — double-curly-brace syntax
-- inside a comment line is still live Jinja, not inert text. That's why
-- this comment spells out function names instead of writing the syntax.

select
    -- your columns here
    event_type,
    (occurred_at AT TIME ZONE 'UTC')::date as utc_date,
    count(event_type) as event_count
    
from {{ ref('stg_events') }}
group by event_type, utc_date
