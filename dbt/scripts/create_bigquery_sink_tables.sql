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
