from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.schemas import Event, EventCreate


class EventRepository(ABC):
    """The port. Services depend on this abstraction, never on a concrete
    implementation (Dependency Inversion) — that's what let Milestone 2's
    InMemoryEventRepository be swapped for a Postgres-backed one without
    touching EventService or the routers at all (Liskov substitutability
    in action). InMemoryEventRepository has since been retired (commented
    out in app/repositories/memory.py) — PostgresEventRepository is the
    only live implementation now — but the abstraction stays, and
    Event.id being service-assigned (app/services/events.py) still traces
    back to it, not to a currently-active swap requirement.

    Kept to exactly what EventService needs (Interface Segregation) — don't
    add methods here "for later"; add them when a real caller needs them.
    """

    @abstractmethod
    async def add(self, tenant_id: UUID, event_in: EventCreate) -> Event:
        """Persist a new event. No id passed in — EventORM generates it via
        server_default (gen_random_uuid()), so this method must read the
        DB-assigned value back and return the full Event, including it.
        Same restructuring TenantAccountRepository.create went through,
        for the same reason (see its docstring).

        event_in.occurred_at must already be resolved (non-None) by the
        caller — EventService.ingest's "default to now if not supplied"
        fallback is a business rule, not database housekeeping, so unlike
        id it's still assigned above this layer, not generated here."""

    @abstractmethod
    async def list(self, tenant_id: UUID, limit: int = 50, offset: int = 0) -> list[Event]:
        """Return a page of events for tenant_id only, most recent first.

        This is the app-layer half of tenant isolation (defense in depth —
        Postgres RLS is the other half, enforced independently at the DB).
        A caller passing the wrong tenant_id here is an app bug; RLS is what
        stops that bug from actually leaking data.
        """
