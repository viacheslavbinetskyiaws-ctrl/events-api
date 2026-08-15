from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.deps import DataQualityServiceDep, SessionDep
from app.domain.schemas import DataQualityReport

router = APIRouter(tags=["health"])


@router.get("/healthz")
def liveness() -> dict[str, str]:
    """k8s liveness probe: is the process up and able to serve requests at
    all? No dependency checks here on purpose — if this fails, k8s kills
    and restarts the pod, which won't fix a downstream DB outage."""
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(session: SessionDep) -> dict[str, str]:
    """k8s readiness probe: can this pod currently serve traffic? From
    Milestone 2 onward this should check DB connectivity; until then
    there's no external dependency to check."""
    stmt = select(1)
    try:
        await session.execute(stmt)
    except Exception as err:
        raise HTTPException(503, "DB is not responding") from err

    return {"status": "ok"}


@router.get("/health/data-quality", response_model=DataQualityReport)
def data_quality(service: DataQualityServiceDep) -> DataQualityReport:
    try:
        report = service.get_latest_report()
    except (FileNotFoundError, ValidationError) as err:
        raise HTTPException(503, "dbt run results unavailable or invalid") from err

    if not report.passed:
        raise HTTPException(503, detail=report)

    return report
