"""Integration test for PostgresAnalyticsRepository — same idea as
test_postgres_repository.py, but for the analytics vertical.

Run with: docker compose up -d postgres && uv run pytest -m integration

Note this test inserts rows into daily_event_counts directly via SQL,
bypassing dbt entirely. That's deliberate: dbt itself never runs against
events_test (see tests/conftest.py's _test_dbt_tables_created), and this
test isn't trying to verify dbt's aggregation logic anyway (that's what
the dbt tests in dbt/models/marts/schema.yml are for) — it's verifying
that PostgresAnalyticsRepository correctly maps whatever rows are in the
table to DailyEventCount objects.

Your turn:

test_get_daily_counts_round_trips:
  - Insert a row or two directly into daily_event_counts via
    `db_session.execute(text("INSERT INTO daily_event_counts ..."))` (or
    build DailyEventCountORM instances and `db_session.add(...)`).
  - `await db_session.commit()`.
  - Build a PostgresAnalyticsRepository(db_session) and call
    `get_daily_counts()`.
  - Assert what comes back matches what you inserted — this is the real
    proof that the ORM <-> domain mapping in the repository is correct.

test_get_daily_counts_orders_most_recent_first:
  - Insert rows for at least two different utc_date values, out of order.
  - Assert `get_daily_counts()` returns them most-recent-first — same
    contract EventRepository.list already has, checked here for
    consistency across both repositories.
"""

from datetime import date
from uuid import uuid4

import pytest

from app.domain.schemas import DailyEventCount
from app.repositories.analytics import PostgresAnalyticsRepository
from app.repositories.models import DailyEventCountORM

pytestmark = pytest.mark.integration

TENANT_ID = uuid4()


async def test_get_daily_counts_round_trips(db_session):
    event_first = DailyEventCount(
        tenant_id=TENANT_ID, event_type="signup", utc_date=date(2026, 11, 11), event_count=2
    )
    event_second = DailyEventCount(
        tenant_id=TENANT_ID, event_type="logout", utc_date=date(2026, 11, 12), event_count=3
    )
    db_session.add(DailyEventCountORM(**event_first.model_dump()))
    db_session.add(DailyEventCountORM(**event_second.model_dump()))
    await db_session.commit()

    repo = PostgresAnalyticsRepository(db_session)
    result = await repo.get_daily_counts(TENANT_ID)

    assert event_first == result[1]
    assert event_second == result[0]


async def test_get_daily_counts_orders_most_recent_first(db_session):
    event_first = DailyEventCount(
        tenant_id=TENANT_ID, event_type="signup", utc_date=date(2026, 11, 11), event_count=2
    )
    event_second = DailyEventCount(
        tenant_id=TENANT_ID, event_type="logout", utc_date=date(2026, 11, 12), event_count=3
    )
    event_third = DailyEventCount(
        tenant_id=TENANT_ID, event_type="signup", utc_date=date(2026, 11, 10), event_count=2
    )
    db_session.add(DailyEventCountORM(**event_first.model_dump()))
    db_session.add(DailyEventCountORM(**event_second.model_dump()))
    db_session.add(DailyEventCountORM(**event_third.model_dump()))

    await db_session.commit()

    repo = PostgresAnalyticsRepository(db_session)
    result = await repo.get_daily_counts(TENANT_ID)

    assert event_second == result[0]
