select
    id,
    tenant_id,
    event_type,
    user_id,
    occurred_at,
    (occurred_at AT TIME ZONE 'UTC')::date as utc_date,
    ingested_at,
    properties

from {{ ref('stg_events') }}
