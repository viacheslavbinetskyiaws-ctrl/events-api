from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class DBTBase(DeclarativeBase):
    """Shared declarative base for all dbt ORM models."""


settings = get_settings()

# echo=False even in dev: SQL statement logging is noisy and better handled
# by SQLAlchemy's own logging config if/when it's actually needed.
engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed after."""
    async with async_session_factory() as session:
        yield session
