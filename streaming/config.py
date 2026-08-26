from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class StreamingSettings(BaseSettings):
    """Config for the CDC consumer — a standalone process, separate from
    the API server (see streaming/consumer.py's module docstring), so
    this is its own Settings class rather than extending
    app.core.config.Settings. Same reasoning dbt/profiles.yml already
    uses for having its own config instead of reusing the API's.

    kafka_bootstrap_servers defaults to localhost:29092 — the
    PLAINTEXT_HOST listener from docker-compose.yml's kafka service, not
    9092 (that one's for containers on the Docker network, like
    kafka-connect; this process runs on the host).

    mongo_uri's localhost:27017 is the same story: docker-compose.yml
    publishes mongodb's 27017 to the host, and this process runs there
    too, not inside the Docker network.
    """

    kafka_bootstrap_servers: str = "localhost:29092"
    tenant_accounts_topic: str = "cdc.public.tenant_accounts"
    events_topic: str = "cdc.public.events"
    consumer_group_id: str = "cdc-audit-consumer"
    log_level: str = "INFO"

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "events_projection"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="STREAMING_")


@cache
def get_streaming_settings() -> StreamingSettings:
    return StreamingSettings()
