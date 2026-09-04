from collections.abc import AsyncGenerator

import boto3
from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class DBTBase(DeclarativeBase):
    """Shared declarative base for all dbt ORM models."""


settings = get_settings()

if settings.environment == "aws":
    if settings.db_host is None:
        raise RuntimeError("APP_DB_HOST must be set when APP_ENVIRONMENT=aws")
    db_host = settings.db_host
    _rds_client = boto3.client("rds", region_name=settings.aws_region)

    url = URL.create(
        "postgresql+asyncpg",
        username=settings.db_user,
        host=db_host,
        port=settings.db_port,
        database=settings.db_name,
    )
    engine = create_async_engine(
        url, echo=False, pool_recycle=600, pool_pre_ping=True, connect_args={"ssl": "require"}
    )

    @event.listens_for(engine.sync_engine, "do_connect")
    def _inject_iam_token(dialect, conn_rec, cargs, cparams):
        cparams["password"] = _rds_client.generate_db_auth_token(
            DBHostname=db_host,
            Port=settings.db_port,
            DBUsername=settings.db_user,
        )
else:
    # echo=False even in dev: SQL statement logging is noisy and better handled
    # by SQLAlchemy's own logging config if/when it's actually needed.
    engine = create_async_engine(settings.database_url, echo=False)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed after."""
    async with async_session_factory() as session:
        yield session
