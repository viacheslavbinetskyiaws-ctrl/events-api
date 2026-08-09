from uuid import UUID

from app.domain.exceptions import DomainError
from app.domain.schemas import TenantAccount, TenantAccountCreate, TenantAccountUpdate
from app.repositories.tenant_accounts import TenantAccountRepository


class TenantAccountService:
    """Business logic for tenant accounts — Milestone 8's CDC write path.
    No X-Tenant-ID involved anywhere here, unlike EventService/
    AnalyticsService: this is what creates/manages tenants, not something
    scoped to one.

    create() validates name isn't blank, then delegates straight to the
    repository — no id/timestamps assigned here, Postgres generates all
    three via server_default (see TenantAccountORM and
    TenantAccountRepository.create's docstrings for why this diverges from
    EventService.ingest's app-side id=uuid4()/occurred_at: no
    InMemoryTenantAccountRepository exists to keep symmetric with).

    update() validates the same thing for the same reason:
    TenantAccountUpdate.name being an empty string, not None, would
    otherwise sail through the repository's exclude_none filtering
    untouched — same class of bug event_type's blank-check guards against
    on the create side. The "does tenant_id exist" check and the actual
    field-merging both belong in the repository (see
    PostgresTenantAccountRepository's docstring), not duplicated here.
    """

    def __init__(self, repository: TenantAccountRepository) -> None:
        self._repository = repository

    async def create(self, tenant_account_in: TenantAccountCreate) -> TenantAccount:
        if tenant_account_in.name == "":
            raise DomainError("name must not be blank")

        return await self._repository.create(tenant_account_in)

    async def update(self, tenant_id: UUID, updates: TenantAccountUpdate) -> TenantAccount:
        if updates.name == "":
            raise DomainError("name must not be blank")

        return await self._repository.update(tenant_id, updates)
