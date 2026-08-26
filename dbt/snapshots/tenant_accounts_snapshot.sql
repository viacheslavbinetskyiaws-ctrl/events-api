{% snapshot tenant_accounts_snapshot %}

{{
    config(
        target_schema='public',
        unique_key='id',
        strategy='timestamp',
        updated_at='updated_at'
    )
}}

select * from {{ source('app', 'tenant_accounts') }}

{% endsnapshot %}
