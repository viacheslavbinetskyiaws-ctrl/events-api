from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas import DailyEventCount
from app.repositories.models import DailyEventCountORM


class AnalyticsRepository(ABC):
    @abstractmethod
    async def get_daily_counts(self) -> list[DailyEventCount]:
        """Get from dbt"""


class PostgresAnalyticsRepository(AnalyticsRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_daily_counts(self) -> list[DailyEventCount]:
        stmt = select(DailyEventCountORM).order_by(DailyEventCountORM.utc_date.desc())

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [DailyEventCount.model_validate(row, from_attributes=True) for row in rows]
