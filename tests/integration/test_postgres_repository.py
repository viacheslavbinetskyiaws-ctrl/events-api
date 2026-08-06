"""Integration test for PostgresEventRepository — the one place a real
Postgres is actually involved. Everything else in the test suite is
allowed to run without Docker; this file is why it's marked separately.

Run with: docker compose up -d postgres && alembic upgrade head && \
          uv run pytest -m integration

Your turn, once PostgresEventRepository.add/list are implemented:

test_add_then_list_round_trips:
  - Build a PostgresEventRepository(db_session).
  - `await repo.add(some_event)`.
  - `await repo.list()` and assert the event comes back with the same
    id/event_type/user_id/occurred_at/properties — this is the real proof
    that the ORM <-> domain mapping in PostgresEventRepository is correct,
    not just that it doesn't raise.

test_list_orders_most_recent_first:
  - Add two events with distinct occurred_at values, out of chronological
    order (add the older one second).
  - Assert `repo.list()` still returns them most-recent-first — this is
    the same contract InMemoryEventRepository.list already satisfies;
    this test is what proves Postgres honors it too (LSP in practice:
    both implementations are interchangeable from the caller's view).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.schemas import Event
from app.repositories.postgres import PostgresEventRepository

pytestmark = pytest.mark.integration


async def test_add_then_list_round_trips(db_session):
    repo = PostgresEventRepository(db_session)

    event = Event(
        id=uuid4(),
        event_type="signup",
        user_id="u1",
        occurred_at=datetime.now(UTC),
        properties={"enabled": True},
    )

    await repo.add(event)

    result = await repo.list()

    assert len(result) == 1
    assert result[0] == event


async def test_list_orders_most_recent_first(db_session):
    repo = PostgresEventRepository(db_session)

    event_first = Event(
        id=uuid4(),
        event_type="signup",
        user_id="u1",
        occurred_at=datetime.now(UTC),
        properties={"enabled": True},
    )

    await repo.add(event_first)

    event_second = Event(
        id=uuid4(),
        event_type="signup",
        user_id="u1",
        occurred_at=datetime.now(UTC) - timedelta(days=2),
        properties={"enabled": True},
    )

    await repo.add(event_second)

    result = await repo.list()

    assert result[0] == event_first
