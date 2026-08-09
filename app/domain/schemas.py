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


class TenantAccountCreate(BaseModel):
    """Input shape for POST /admin/tenants. No `id`/timestamps — assigned
    by the service, same convention as EventCreate."""

    name: str
    plan_tier: str


class TenantAccountUpdate(BaseModel):
    """Input shape for PATCH /admin/tenants/{tenant_id} — partial update.
    Both fields optional; None means "leave unchanged", not "clear the
    field" (neither column is nullable, so there's no other sensible
    meaning for None here)."""

    name: str | None = None
    plan_tier: str | None = None


class TenantAccount(BaseModel):
    """The domain entity — this is the write path Milestone 8's CDC
    connector captures."""

    id: UUID
    name: str
    plan_tier: str
    created_at: datetime
    updated_at: datetime
