from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas import DailyEventCount
from app.repositories.models import DailyEventCountORM


class AnalyticsRepository(ABC):
    @abstractmethod
    async def get_daily_counts(self, tenant_id: UUID) -> list[DailyEventCount]:
        """Get from dbt, scoped to tenant_id only — app-layer half of the
        same defense-in-depth this table's Postgres RLS policy also
        enforces (see dbt/dbt_project.yml's marts +post-hook)."""


class PostgresAnalyticsRepository(AnalyticsRepository):
    """Your turn: add `.where(DailyEventCountORM.tenant_id == tenant_id)`
    to the existing select, same shape as PostgresEventRepository.list's
    filter."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_daily_counts(self, tenant_id: UUID) -> list[DailyEventCount]:
        stmt = (
            select(DailyEventCountORM)
            .where(DailyEventCountORM.tenant_id == tenant_id)
            .order_by(DailyEventCountORM.utc_date.desc())
        )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [DailyEventCount.model_validate(row, from_attributes=True) for row in rows]
