from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas import Event
from app.repositories.base import EventRepository
from app.repositories.models import EventORM


class PostgresEventRepository(EventRepository):
    """The real adapter — same contract as InMemoryEventRepository, just
    backed by Postgres via an async SQLAlchemy session. This is the payoff
    of depending on the EventRepository abstraction everywhere else: swap
    this in for InMemoryEventRepository in app/api/deps.py and nothing in
    EventService or the routers has to change.

    Note EventRepository's methods aren't declared `async` on the ABC —
    but nothing stops the implementation from being async; the router
    already awaits service calls the same way regardless. If this ends up
    awkward, that's worth flagging: it may mean the abstraction needs to
    declare async signatures.

    add(event) needed no change for Milestone 7 — event.tenant_id is
    already set by EventService before this is called, and model_dump()
    picks it up like any other field.

    list(tenant_id, limit, offset) is your turn to update: add
    `.where(EventORM.tenant_id == tenant_id)` to the existing select(),
    ideally combined with the `.order_by(...)` chain rather than as a
    separate statement. This is the app-layer half of tenant isolation —
    RLS enforces the same boundary independently at the DB, but this app-
    layer filter still has to be right (defense in depth, not either/or).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: Event) -> Event:
        self._session.add(EventORM(**event.model_dump()))
        await self._session.commit()

        return event

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
