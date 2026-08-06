from fastapi import APIRouter

from app.api.deps import AnalyticsServiceDep
from app.domain.schemas import DailyEventCount

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/daily", response_model=list[DailyEventCount])
async def list_daily_event_counts(service: AnalyticsServiceDep) -> list[DailyEventCount]:
    return await service.get_daily_counts()
