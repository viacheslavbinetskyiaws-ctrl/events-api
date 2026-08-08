from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    """Input shape for POST /events. No `id`/`occurred_at` — those are
    assigned by the service, not the caller."""

    event_type: str
    user_id: str
    occurred_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    """The domain entity. Doubles as the API read/response model for now —
    a stricter design would keep this framework-agnostic (e.g. a dataclass)
    and separate from any Pydantic I/O schema, but for this project's scope
    that split isn't earning its complexity yet."""

    id: UUID
    tenant_id: UUID
    event_type: str
    user_id: str
    occurred_at: datetime
    properties: dict[str, Any] = Field(default_factory=dict)


class DailyEventCount(BaseModel):
    tenant_id: UUID
    event_type: str
    utc_date: date
    event_count: int
