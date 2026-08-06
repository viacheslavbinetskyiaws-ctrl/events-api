from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, sourced from environment variables (12-factor config).

    Every field here is overridable via an `APP_`-prefixed env var, e.g.
    APP_LOG_LEVEL=DEBUG. `database_url` defaults to the docker-compose
    Postgres service for local dev; override it for anything else (tests,
    CI, k8s, real AWS).
    """

    app_name: str = "events-api"
    environment: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://events:events@localhost:5432/events"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
