from datetime import UTC, datetime
from uuid import uuid4

from app.domain.exceptions import DomainError
from app.domain.schemas import Event, EventCreate
from app.repositories.base import EventRepository


class EventService:
    """Business logic for events. Depends on the EventRepository
    abstraction (constructor injection), never a concrete class — that's
    the Dependency Inversion Principle: this class doesn't know or care
    whether events end up in memory, Postgres, or anywhere else.

    Your turn to implement `ingest` and `list_events`. Things to decide
    and get right (I'll review these against SOLID/production concerns):

    ingest(event_in):
      - event_in has no `id` or `occurred_at` (see EventCreate) — this is
        the layer that assigns them. Generate an id (uuid4), default
        occurred_at to now (UTC) if the caller didn't supply one.
      - Where would you validate event_type isn't empty/blank? (Hint: this
        is business logic, so it belongs here, not in the router or the
        repository.) Raise a DomainError subclass on failure rather than
        an HTTPException — routers/main.py map domain errors to HTTP.
      - Build the full Event domain object and hand it to the repository.

    list_events(limit, offset):
      - Delegate to the repository, but this is also the right place for
        any business rule around pagination (e.g. clamping an unreasonable
        limit) rather than trusting the caller blindly.
    """

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    async def ingest(self, event_in: EventCreate) -> Event:
        if event_in.event_type is None or event_in.event_type == "":
            raise DomainError("event_type must not be blank")
        event_out = Event(
            id=uuid4(),
            occurred_at=event_in.occurred_at or datetime.now(UTC),
            **event_in.model_dump(exclude={"occurred_at"}),
        )
        await self._repository.add(event_out)

        return event_out

    async def list_events(self, limit: int = 50, offset: int = 0) -> list[Event]:
        if offset < 0 or limit < 0 or limit > 50:
            raise DomainError("offset or limit values are invalid")

        return await self._repository.list(limit, offset)
