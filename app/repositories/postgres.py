from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas import Event, EventCreate
from app.repositories.base import EventRepository
from app.repositories.models import EventORM


class PostgresEventRepository(EventRepository):
    """The real adapter — same contract InMemoryEventRepository (now
    retired, see app/repositories/memory.py) used to satisfy, just backed
    by Postgres via an async SQLAlchemy session instead. This was the
    payoff of depending on the EventRepository abstraction everywhere
    else: it's what let this get wired into app/api/deps.py in place of
    the in-memory one without EventService or the routers having to
    change at all.

    Note EventRepository's methods aren't declared `async` on the ABC —
    but nothing stops the implementation from being async; the router
    already awaits service calls the same way regardless. If this ends up
    awkward, that's worth flagging: it may mean the abstraction needs to
    declare async signatures.

    add(tenant_id, event_in) builds the row without id — server_default
    handles it on INSERT. refresh() is technically redundant on the
    INSERT path (SQLAlchemy's eager_defaults "auto" fetches server_default
    values via RETURNING as part of the INSERT itself, confirmed for
    TenantAccountORM.id/created_at/updated_at), but kept for the same
    explicitness reason as PostgresTenantAccountRepository.create.

    list(tenant_id, limit, offset) filters with
    `.where(EventORM.tenant_id == tenant_id)`, combined with the
    `.order_by(...)` chain. This is the app-layer half of tenant
    isolation — RLS enforces the same boundary independently at the DB,
    but this app-layer filter still has to be right (defense in depth,
    not either/or).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant_id: UUID, event_in: EventCreate) -> Event:
        row = EventORM(tenant_id=tenant_id, **event_in.model_dump())
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)

        return Event.model_validate(row, from_attributes=True)

    async def list(self, tenant_id: UUID, limit: int = 50, offset: int = 0) -> list[Event]:
        stmt = (
            select(EventORM)
            .where(EventORM.tenant_id == tenant_id)
            .order_by(EventORM.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [Event.model_validate(row, from_attributes=True) for row in rows]
