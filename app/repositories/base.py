from abc import ABC, abstractmethod

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
        """Persist a fully-formed Event and return it."""

    @abstractmethod
    async def list(self, limit: int = 50, offset: int = 0) -> list[Event]:
        """Return a page of events, most recent first."""
