"""Composition root: the one place concrete classes get instantiated and
bound to the abstractions the rest of the app depends on. If you ever find
yourself importing a concrete EventRepository implementation anywhere
outside this file, that's a DIP violation creeping in — everything else
should depend on the EventRepository abstraction only.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_db_session
from app.repositories.analytics import AnalyticsRepository, PostgresAnalyticsRepository
from app.repositories.base import EventRepository
from app.repositories.postgres import PostgresEventRepository
from app.repositories.tenant_accounts import (
    PostgresTenantAccountRepository,
    TenantAccountRepository,
)
from app.services.analytics import AnalyticsService
from app.services.data_quality import DataQualityService
from app.services.events import EventService
from app.services.tenant_accounts import TenantAccountService

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# Shared with app/api/routers/events.py, which also needs the resolved
# tenant to pass into EventService (business-rule validation there is
# separate from — and does not replace — the RLS activation below).
TenantIdHeader = Annotated[UUID | None, Header(alias="X-Tenant-ID")]


async def get_tenant_scoped_session(
    session: SessionDep, x_tenant_id: TenantIdHeader = None
) -> AsyncSession:
    """Activates RLS for this session/transaction: sets the Postgres GUC
    the `tenant_isolation` policy reads (migration d7a67740cfa5).

    set_config(name, value, is_local), not a raw `SET LOCAL ... = :val`
    string — Postgres doesn't bind-parameter SET statements the normal
    way, but set_config is a real function call and takes a real bind
    parameter. No string interpolation of the tenant value.

    is_local=true (SET LOCAL semantics, third arg) matters specifically
    because the engine pools physical connections: this value must not
    outlive the current transaction, or a pooled connection later handed
    to a *different* tenant's request would silently inherit it.
    SQLAlchemy's AsyncSession autobegins a transaction on first use —
    since this runs before any repository query, the SET and the query
    that follows it share that same transaction, so the setting is
    visible exactly where it needs to be and nowhere else.

    x_tenant_id is UUID | None (not enforced here) because EventService's
    own check is what actually rejects a missing tenant with a clean
    DomainError before a repository call ever happens. If that check were
    ever bypassed, RLS's fail-closed default (zero rows when
    app.current_tenant is unset) is the backstop, not a crash in this
    dependency.
    """
    if x_tenant_id is not None:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
            {"tenant_id": str(x_tenant_id)},
        )

    return session


TenantScopedSessionDep = Annotated[AsyncSession, Depends(get_tenant_scoped_session)]


def get_event_repository(session: TenantScopedSessionDep) -> EventRepository:
    return PostgresEventRepository(session)


EventRepositoryDep = Annotated[EventRepository, Depends(get_event_repository)]


def get_event_service(repository: EventRepositoryDep) -> EventService:
    return EventService(repository)


EventServiceDep = Annotated[EventService, Depends(get_event_service)]


def get_analytics_repository(session: TenantScopedSessionDep) -> AnalyticsRepository:
    return PostgresAnalyticsRepository(session)


AnalyticsRepositoryDep = Annotated[AnalyticsRepository, Depends(get_analytics_repository)]


def get_analytics_service(repository: AnalyticsRepositoryDep) -> AnalyticsService:
    return AnalyticsService(repository)


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


# Plain SessionDep, not TenantScopedSessionDep — tenant_accounts isn't a
# tenant-scoped resource (see TenantAccountRepository's docstring), so
# there's no app.current_tenant to set here.
def get_tenant_account_repository(session: SessionDep) -> TenantAccountRepository:
    return PostgresTenantAccountRepository(session)


TenantAccountRepositoryDep = Annotated[
    TenantAccountRepository, Depends(get_tenant_account_repository)
]


def get_tenant_account_service(repository: TenantAccountRepositoryDep) -> TenantAccountService:
    return TenantAccountService(repository)


TenantAccountServiceDep = Annotated[TenantAccountService, Depends(get_tenant_account_service)]

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_data_quality_service(settings: SettingsDep) -> DataQualityService:
    return DataQualityService(settings)


DataQualityServiceDep = Annotated[DataQualityService, Depends(get_data_quality_service)]
