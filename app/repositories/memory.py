# Unused — this project only ever wires PostgresEventRepository in
# app/api/deps.py, never this one. Kept commented out rather than deleted
# since other docstrings (EventRepository, PostgresEventRepository,
# TenantAccountRepository) reference it as the reason id generation lives
# in EventService rather than the repository layer.
#
# from app.domain.schemas import Event
# from app.repositories.base import EventRepository
#
#
# class InMemoryEventRepository(EventRepository):
#     """Placeholder adapter so the app is runnable before Postgres exists
#     (Milestone 2). Your turn to implement `add` and `list` below.
#
#     Notes / constraints to satisfy:
#     - Must conform to EventRepository's contract (same signatures/behavior
#       callers can rely on) — that's what LSP means in practice here.
#     - `list` should return most-recent-first, respecting limit/offset.
#     - This is single-process, in-memory storage: a plain list or dict
#       keyed by event id is enough. No need for thread-safety/locking for
#       this project's scope.
#     """
#
#     def __init__(self) -> None:
#         self._events: list[Event] = []
#
#     async def add(self, event: Event) -> Event:
#         self._events.append(event)
#
#         return event
#
#     async def list(self, limit: int = 50, offset: int = 0) -> list[Event]:
#         sorted_events = sorted(self._events, key=lambda x: (x.occurred_at, x.id), reverse=True)
#         paginated_events = sorted_events[offset : offset + limit]
#
#         return paginated_events
