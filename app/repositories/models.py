import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, DBTBase


class EventORM(Base):
    """Table definition only — mapping ORM rows to/from the `Event` domain
    schema is PostgresEventRepository's job, not this class's. Keeping the
    two separate (rather than making Event itself the ORM model) is what
    lets the domain layer stay ignorant of SQLAlchemy entirely.
    """

    __tablename__ = "events"
    __table_args__ = (
        # Every list() call filters by tenant_id (app layer and/or RLS) and
        # orders by occurred_at desc — this composite index serves both in
        # one index scan instead of a filter-then-sort as data grows.
        Index("ix_events_tenant_occurred_at", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)


class DailyEventCountORM(DBTBase):
    __tablename__ = "daily_event_counts"

    event_type: Mapped[str] = mapped_column(primary_key=True)
    utc_date: Mapped[date] = mapped_column(Date(), primary_key=True)
    event_count: Mapped[int] = mapped_column()
