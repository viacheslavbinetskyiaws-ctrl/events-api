from app.domain.schemas import DailyEventCount
from app.repositories.analytics import AnalyticsRepository


class AnalyticsService:
    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def get_daily_counts(self) -> list[DailyEventCount]:
        return await self._repository.get_daily_counts()
