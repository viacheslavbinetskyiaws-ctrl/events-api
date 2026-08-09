from uuid import UUID


class DomainError(Exception):
    """Base class for errors raised by the service layer. Routers never
    raise HTTPException directly for domain-rule violations — they let
    these propagate and a FastAPI exception handler (in main.py) maps them
    to HTTP responses. Keeps the service layer free of any HTTP concept."""


class EventNotFoundError(DomainError):
    def __init__(self, event_id: UUID) -> None:
        self.event_id = event_id
        super().__init__(f"Event {event_id} not found")


class TenantAccountNotFoundError(DomainError):
    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        super().__init__(f"Tenant account {tenant_id} not found")
