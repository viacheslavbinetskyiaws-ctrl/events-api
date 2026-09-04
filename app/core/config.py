from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, sourced from environment variables (12-factor config).

    Every field here is overridable via an `APP_`-prefixed env var, e.g.
    APP_LOG_LEVEL=DEBUG. `database_url` defaults to the docker-compose
    Postgres service for local dev; override it for anything else (tests,
    CI, k8s, real AWS).

    Connects as `events_app`, not the `events` owner role — since Milestone
    7's RLS policy on `events`, the app is meant to run under enforced
    row-level security. Migrations and dbt still connect as the owner role
    (see migrations/env.py, dbt/profiles.yml) since they need DDL rights
    and/or cross-tenant reads RLS would otherwise block.
    """

    app_name: str = "events-api"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://events_app:events_app@localhost:5432/events"
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "events"
    db_user: str = "events_app"
    aws_region: str | None = None
    dbt_run_results_path: str = "../dbt/target/run_results.json"
    dbt_sources_path: str = "../dbt/target/sources.json"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")


@cache
def get_settings() -> Settings:
    return Settings()
