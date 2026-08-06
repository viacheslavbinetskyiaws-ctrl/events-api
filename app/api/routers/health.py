from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import SessionDep

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
