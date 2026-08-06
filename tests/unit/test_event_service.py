"""Unit tests for EventService. These never touch a real repository —
`mock_repository` (see tests/conftest.py) stands in for EventRepository,
which is only possible because EventService depends on the abstraction,
not a concrete class (DIP). That's what "unit" means here: only
EventService's own logic is under test.

Your turn to write the test bodies. Cases worth covering:

test_ingest_calls_repository_add:
  - Call `await service.ingest(EventCreate(event_type="signup", user_id="u1"))`.
  - Assert `mock_repository.add.assert_awaited_once()`.
  - Assert the Event passed to `add` has the fields you'd expect (a real
    UUID id, occurred_at defaulted to ~now since none was supplied).

test_ingest_honors_supplied_occurred_at:
  - Same as above but pass an explicit `occurred_at` in EventCreate.
  - Assert the Event passed to `mock_repository.add` has that exact
    occurred_at, not "now" (this is the bug that got fixed earlier —
    a regression test locks it in).

test_ingest_rejects_blank_event_type:
  - Call ingest with `event_type=""`.
  - Assert it raises DomainError (pytest.raises).
  - Assert `mock_repository.add` was never called (`assert_not_awaited`)
    — a rejected event must not reach storage.

test_list_events_rejects_negative_offset:
  - Assert `list_events(offset=-1)` raises DomainError.

test_list_events_delegates_to_repository:
  - Assert `list_events(limit=10, offset=5)` calls
    `mock_repository.list.assert_awaited_once_with(10, 5)`.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.exceptions import DomainError
from app.domain.schemas import EventCreate
from app.services.events import EventService


async def test_ingest_calls_repository_add(mock_repository):
    service = EventService(mock_repository)

    result = await service.ingest(EventCreate(event_type="signup", user_id="u1"))

    mock_repository.add.assert_awaited_once()
    assert isinstance(result.id, UUID)


async def test_ingest_honors_supplied_occurred_at(mock_repository):
    service = EventService(mock_repository)

    occurred_at = datetime.now(UTC)
    result = await service.ingest(
        EventCreate(event_type="signup", user_id="u1", occurred_at=occurred_at)
    )

    mock_repository.add.assert_awaited_once()

    assert result.occurred_at == occurred_at


async def test_ingest_rejects_blank_event_type(mock_repository):
    service = EventService(mock_repository)

    with pytest.raises(DomainError) as e_info:
        await service.ingest(EventCreate(event_type="", user_id="u1"))

    assert str(e_info.value) == "event_type must not be blank"

    mock_repository.add.assert_not_awaited()


async def test_list_events_rejects_negative_offset(mock_repository):
    service = EventService(mock_repository)

    with pytest.raises(DomainError) as e_info:
        await service.list_events(offset=-1)

    assert str(e_info.value) == "offset or limit values are invalid"


async def test_list_events_delegates_to_repository(mock_repository):
    service = EventService(mock_repository)

    await service.list_events(limit=10, offset=5)

    mock_repository.list.assert_awaited_once_with(10, 5)
