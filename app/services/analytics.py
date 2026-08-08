from uuid import UUID

from app.domain.exceptions import DomainError
from app.domain.schemas import DailyEventCount
from app.repositories.analytics import AnalyticsRepository


class AnalyticsService:
    """x_tenant_id: UUID | None, same contract as EventService's methods —
    FastAPI already rejects a malformed X-Tenant-ID before this runs (see
    app/api/routers/analytics.py). Your turn: reject None with a
    DomainError (same message EventService uses is fine — same rule),
    then pass the UUID through to self._repository.get_daily_counts.
    """

    def __init__(self, repository: AnalyticsRepository) -> None:
        self._repository = repository

    async def get_daily_counts(self, x_tenant_id: UUID | None) -> list[DailyEventCount]:
        if x_tenant_id is None:
            raise DomainError("X-Tenant-ID header is required")

        return await self._repository.get_daily_counts(x_tenant_id)
