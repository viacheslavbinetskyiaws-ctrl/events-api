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

    Your turn to implement `add` and `list`:

    add(event):
      - Build an EventORM from the Event domain object (they have the same
        fields — this is the ORM-row <-> domain-object translation this
        class exists to own).
      - `session.add(...)`, then `await session.commit()`.
      - Return the Event that was passed in (or re-derive it from the ORM
        row — either is defensible; know which you picked and why).

    list(limit, offset):
      - `select(EventORM)` ordered by `occurred_at` descending (most-recent
        first — same contract as InMemoryEventRepository), with
        `.limit(limit).offset(offset)`.
      - `await session.execute(...)`, then map each EventORM row back to
        an `Event` domain object. Don't return ORM instances directly —
        that would leak a SQLAlchemy type across the repository boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: Event) -> Event:
        self._session.add(EventORM(**event.model_dump()))
        await self._session.commit()

        return event

    async def list(self, limit: int = 50, offset: int = 0) -> list[Event]:
        stmt = select(EventORM).order_by(EventORM.occurred_at.desc()).limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [Event.model_validate(row, from_attributes=True) for row in rows]
