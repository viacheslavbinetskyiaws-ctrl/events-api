import os
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import DBTBase
from app.repositories import (
    models,  # noqa: F401  (registers DailyEventCountORM on DBTBase.metadata)
)
from app.repositories.analytics import AnalyticsRepository
from app.repositories.base import EventRepository

# A separate database from the one local dev/manual testing uses (see
# docker/init-test-db.sql) — so running the test suite can never collide
# with or wipe out data you're poking at by hand in another terminal.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://events:events@localhost:5432/events_test"
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture
def mock_repository() -> AsyncMock:
    """An AsyncMock constrained to EventRepository's actual methods (`spec=`
    means calling a typo'd or nonexistent method raises immediately, same
    protection a real subclass gets). Use this for EventService unit tests:
    it isolates the service's business logic from any real storage, and
    lets you assert exactly how the service called its dependency —
    e.g. `mock_repository.add.assert_awaited_once_with(...)`.
    """
    return AsyncMock(spec=EventRepository)


@pytest.fixture
def mock_analytics_repository() -> AsyncMock:
    """Same idea as mock_repository, but spec'd to AnalyticsRepository.
    Use this for AnalyticsService unit tests.
    """
    return AsyncMock(spec=AnalyticsRepository)


@pytest.fixture(scope="session")
def _test_db_migrated() -> None:
    """Applies Alembic migrations to events_test once per test session, so
    integration tests never depend on a developer remembering to run
    `alembic upgrade head` against the test database specifically (only
    the dev one is documented/expected to be migrated by hand).
    """
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DATABASE_URL
    try:
        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        upgrade(Config(str(alembic_ini)), "head")
    finally:
        del os.environ["ALEMBIC_DATABASE_URL"]


@pytest_asyncio.fixture(scope="session")
async def _test_dbt_tables_created() -> None:
    """Creates dbt-owned tables (daily_event_counts) in events_test.

    dbt itself never touches events_test — it only ever targets the dev
    database (see dbt/profiles.yml) — so nothing else would create this
    table here otherwise. Uses DBTBase.metadata directly, bypassing both
    Alembic (which deliberately never sees dbt-owned tables) and dbt
    itself (which never runs against this database): the integration test
    only needs the table's shape to exist, not a real dbt run.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(DBTBase.metadata.create_all)


@pytest_asyncio.fixture
async def db_session(
    _test_db_migrated: None, _test_dbt_tables_created: None
) -> AsyncGenerator[AsyncSession]:
    """A real session against events_test — a separate database from the
    one local dev/manual testing uses, so the two can never collide (see
    TEST_DATABASE_URL above). Truncates `events` and `daily_event_counts`
    both before and after each test — before, so a test never starts
    against a dirty table (a crashed prior run, for instance); after, so
    nothing is left behind for the next run. Requires
    `docker compose up -d postgres` — schema setup for both tables is
    applied automatically via the two fixtures above.
    """
    async with test_session_factory() as session:
        await session.execute(text("TRUNCATE TABLE events"))
        await session.execute(text("TRUNCATE TABLE daily_event_counts"))
        await session.commit()

        yield session

        await session.rollback()
        await session.execute(text("TRUNCATE TABLE events"))
        await session.execute(text("TRUNCATE TABLE daily_event_counts"))
        await session.commit()
