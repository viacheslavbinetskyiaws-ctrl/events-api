from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.schemas import Event


class EventRepository(ABC):
    """The port. Services depend on this abstraction, never on a concrete
    implementation (Dependency Inversion) — that's what lets Milestone 2
    swap InMemoryEventRepository for a Postgres-backed one without touching
    EventService or the routers at all (Liskov substitutability in action).

    Kept to exactly what EventService needs (Interface Segregation) — don't
    add methods here "for later"; add them when a real caller needs them.
    """

    @abstractmethod
    async def add(self, event: Event) -> Event:
        """Persist a fully-formed Event and return it. No separate tenant_id
        parameter — `event.tenant_id` is already set by the service, so it
        travels with the object like every other field."""

    @abstractmethod
    async def list(self, tenant_id: UUID, limit: int = 50, offset: int = 0) -> list[Event]:
        """Return a page of events for tenant_id only, most recent first.

        This is the app-layer half of tenant isolation (defense in depth —
        Postgres RLS is the other half, enforced independently at the DB).
        A caller passing the wrong tenant_id here is an app bug; RLS is what
        stops that bug from actually leaking data.
        """
