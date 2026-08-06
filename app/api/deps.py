"""Composition root: the one place concrete classes get instantiated and
bound to the abstractions the rest of the app depends on. If you ever find
yourself importing a concrete EventRepository implementation anywhere
outside this file, that's a DIP violation creeping in — everything else
should depend on the EventRepository abstraction only.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.repositories.analytics import AnalyticsRepository, PostgresAnalyticsRepository
from app.repositories.base import EventRepository
from app.repositories.postgres import PostgresEventRepository
from app.services.analytics import AnalyticsService
from app.services.events import EventService

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_event_repository(session: SessionDep) -> EventRepository:
    return PostgresEventRepository(session)


EventRepositoryDep = Annotated[EventRepository, Depends(get_event_repository)]


def get_event_service(repository: EventRepositoryDep) -> EventService:
    return EventService(repository)


EventServiceDep = Annotated[EventService, Depends(get_event_service)]


def get_analytics_repository(session: SessionDep) -> AnalyticsRepository:
    return PostgresAnalyticsRepository(session)


AnalyticsRepositoryDep = Annotated[AnalyticsRepository, Depends(get_analytics_repository)]


def get_analytics_service(repository: AnalyticsRepositoryDep) -> AnalyticsService:
    return AnalyticsService(repository)


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]
