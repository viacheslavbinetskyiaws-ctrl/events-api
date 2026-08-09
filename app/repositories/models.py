import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, FetchedValue, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, DBTBase


class EventORM(Base):
    """Table definition only — mapping ORM rows to/from the `Event` domain
    schema is PostgresEventRepository's job, not this class's. Keeping the
    two separate (rather than making Event itself the ORM model) is what
    lets the domain layer stay ignorant of SQLAlchemy entirely.

    `id` is DB-generated (server_default=gen_random_uuid()), same as
    TenantAccountORM.id — EventRepository's only remaining implementation
    is Postgres (InMemoryEventRepository has been retired, see
    app/repositories/memory.py), so there's no longer a substitutability
    reason to assign it app-side. occurred_at stays app-assigned though:
    its "default to now if not supplied" behavior is a business rule
    (see EventService.ingest), not database housekeeping like id/
    created_at/updated_at are — that distinction, not the retirement of
    InMemoryEventRepository, is why it's treated differently.
    """

    __tablename__ = "events"
    __table_args__ = (
        # Every list() call filters by tenant_id (app layer and/or RLS) and
        # orders by occurred_at desc — this composite index serves both in
        # one index scan instead of a filter-then-sort as data grows.
        Index("ix_events_tenant_occurred_at", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column()
    event_type: Mapped[str] = mapped_column(index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)


class TenantAccountORM(Base):
    """Milestone 8's CDC source table — normal CRUD via the (yet to be
    written) admin endpoint, captured downstream by Debezium. `id` is the
    same UUID space as events.tenant_id/daily_event_counts.tenant_id: this
    table is the tenant registry, its primary key is the canonical tenant
    identifier used everywhere else. `id` is DB-generated
    (server_default=gen_random_uuid(), builtin since Postgres 13, no
    pgcrypto extension needed) — deliberately NOT matching EventORM's
    convention of app-side id generation. That convention exists to keep
    InMemoryEventRepository (since retired, see app/repositories/memory.py)
    and PostgresEventRepository substitutable; there's no
    InMemoryTenantAccountRepository (only Postgres ever implemented
    TenantAccountRepository), so nothing forces id generation up into the
    service here the way it did for Event.id.

    Judgment calls worth a second look, not settled by PLAN.md: `name`
    unique (no two tenants sharing a name — reasonable for a registry, but
    a real constraint, not a hedge), `plan_tier` a plain string rather than
    an enum (YAGNI — same reasoning as event_type staying unconstrained
    until there's an actual fixed vocabulary to enforce), created_at/
    updated_at server-side since these are audit timestamps the server
    owns, not client-supplied like Event.occurred_at is. created_at uses
    server_default=func.now() (INSERT-only, standard); updated_at is
    trigger-owned (migration 95ac45427c05), not server_default/onupdate —
    a bump-on-every-write requirement can't be expressed as "the value to
    use when the client omits one," which is all server_default/onupdate
    actually mean; it needs something that unconditionally overwrites
    regardless of what the writer sent, which only a trigger can do.
    """

    __tablename__ = "tenant_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(unique=True)
    plan_tier: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # server_onupdate=FetchedValue() — not onupdate=func.now() — because
    # the value is no longer computed by SQLAlchemy at all: a Postgres
    # trigger (migration 95ac45427c05) sets it on every UPDATE regardless
    # of writer. FetchedValue() tells SQLAlchemy "something server-side
    # (not me) sets this on update, fetch the result back" rather than
    # having SQLAlchemy emit its own value client-side — the distinction
    # that was the actual bug: onupdate=func.now() only fired when
    # SQLAlchemy itself built the UPDATE statement, so a raw SQL UPDATE
    # (confirmed live during Milestone 8 CDC testing) silently left this
    # column stale.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=FetchedValue()
    )


class DailyEventCountORM(DBTBase):
    """Read shape only — dbt owns this table's actual DDL (see
    dbt/models/marts/daily_event_counts.sql), not Alembic. This class just
    has to match whatever dbt actually creates, column for column.
    """

    __tablename__ = "daily_event_counts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(primary_key=True)
    utc_date: Mapped[date] = mapped_column(Date(), primary_key=True)
    event_count: Mapped[int] = mapped_column()
