from fastapi import APIRouter

from app.api.deps import EventServiceDep, TenantIdHeader
from app.domain.schemas import Event, EventCreate

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=Event, status_code=201)
async def create_event(
    event_in: EventCreate, service: EventServiceDep, x_tenant_id: TenantIdHeader = None
) -> Event:
    return await service.ingest(event_in, x_tenant_id)


@router.get("", response_model=list[Event])
async def list_events(
    service: EventServiceDep,
    x_tenant_id: TenantIdHeader = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Event]:
    return await service.list_events(x_tenant_id, limit=limit, offset=offset)
