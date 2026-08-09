from datetime import UTC, datetime
from uuid import UUID

from app.domain.exceptions import DomainError
from app.domain.schemas import Event, EventCreate
from app.repositories.base import EventRepository


class EventService:
    """Business logic for events. Depends on the EventRepository
    abstraction (constructor injection), never a concrete class — that's
    the Dependency Inversion Principle: this class doesn't know or care
    whether events end up in memory, Postgres, or anywhere else.

    Both methods now take x_tenant_id: UUID | None. FastAPI already
    rejects a malformed X-Tenant-ID before this is ever called (its own
    422, same as a bad `?limit=abc` already gets) — see
    app/api/routers/events.py. What's left here is purely the business
    rule that a tenant must be supplied at all: None -> raise DomainError,
    same shape as the event_type/pagination checks already below. No
    parsing needed; by the time this runs, a non-None value is already a
    real UUID.

    ingest() resolves occurred_at's "default to now if not supplied"
    fallback here, not in the repository — that's a business rule, not
    database housekeeping, unlike id (see EventORM's docstring for that
    distinction). id itself is no longer assigned here at all:
    EventRepository.add takes an EventCreate + tenant_id and returns the
    full Event, DB-assigned id included.
    """

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository

    async def ingest(self, event_in: EventCreate, x_tenant_id: UUID | None) -> Event:
        if event_in.event_type is None or event_in.event_type == "":
            raise DomainError("event_type must not be blank")
        if x_tenant_id is None:
            raise DomainError("X-Tenant-ID header is required")

        resolved_event_in = event_in.model_copy(
            update={"occurred_at": event_in.occurred_at or datetime.now(UTC)}
        )

        return await self._repository.add(x_tenant_id, resolved_event_in)

    async def list_events(
        self, x_tenant_id: UUID | None, limit: int = 50, offset: int = 0
    ) -> list[Event]:
        if offset < 0 or limit < 0 or limit > 50:
            raise DomainError("offset or limit values are invalid")

        if x_tenant_id is None:
            raise DomainError("X-Tenant-ID header is required")

        return await self._repository.list(x_tenant_id, limit, offset)
