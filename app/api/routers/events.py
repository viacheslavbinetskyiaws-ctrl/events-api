from fastapi import APIRouter

from app.api.deps import EventServiceDep
from app.domain.schemas import Event, EventCreate

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=Event, status_code=201)
async def create_event(event_in: EventCreate, service: EventServiceDep) -> Event:
    return await service.ingest(event_in)


@router.get("", response_model=list[Event])
async def list_events(service: EventServiceDep, limit: int = 50, offset: int = 0) -> list[Event]:
    return await service.list_events(limit=limit, offset=offset)
