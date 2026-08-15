select 1
where
    (
        select count(*) from {{ source('app', 'events') }}
        where occurred_at > current_timestamp - interval '7 days'
    ) > 0
    and
    (
        select count(*) from {{ source('app', 'events') }}
        where ingested_at > current_timestamp - interval '1 hour'
    ) = 0
