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
--
-- Milestone 7: the grain changes from (event_type, utc_date) to
-- (tenant_id, event_type, utc_date) — add tenant_id to both the select
-- list and the group by. Without it, this aggregates across all tenants
-- into one number per (event_type, utc_date), which is exactly the
-- cross-tenant leak GET /analytics/daily isn't supposed to have.
-- Milestone 11: incremental materialization. unique_key matches the grain
-- already tested in schema.yml (tenant_id, event_type, utc_date);
-- incremental_strategy='merge' does a real Postgres MERGE (15+, we're on
-- 17) keyed on that tuple — a matched row gets REPLACED wholesale, not
-- added to. That's why the is_incremental() filter below re-includes the
-- most-recently-loaded day in full (>=, not >): a naive ">" filter would
-- merge in only that day's *new* partial count on a later run and
-- silently overwrite the previously-computed total, undercounting rather
-- than erroring. Trade-off written down, not hidden: this only catches
-- late-arriving events within one day's grace period of the last run — an
-- event landing for a day older than that boundary is silently missed. A
-- wider lookback window would catch more lateness at the cost of
-- rescanning more of stg_events on every run; one day is the smallest
-- change that fixes the specific replace-not-add merge bug, not a general
-- lateness guarantee.
--
-- Deliberately hand-rolled (is_incremental() + unique_key), not dbt's
-- microbatch incremental strategy (event_time/batch_size/lookback config,
-- available here — dbt-core>=1.9 needed, we're on 1.12) even though
-- microbatch exists specifically to solve this exact late-arrival problem
-- and would need none of the logic above. The point of this milestone was
-- deriving the failure mode by hand, not reaching for the feature that
-- automates it away.
{{
    config(
        materialized = 'incremental',
        unique_key = ['tenant_id', 'event_type', 'utc_date'],
        incremental_strategy='merge',
        contract = { 'enforced': true },
        on_schema_change = 'fail'
    )
}}

{% if is_incremental() %}
with already_loaded as (
    select max(utc_date) as loaded_through_date from {{ this }}
)
{% endif %}

select
    tenant_id,
    event_type,
    utc_date,
    count(event_type) as event_count

from {{ ref('int_events_daily') }}

{% if is_incremental() %}
where utc_date >= (
    select loaded_through_date from already_loaded
)
{% endif %}

group by tenant_id, event_type, utc_date
