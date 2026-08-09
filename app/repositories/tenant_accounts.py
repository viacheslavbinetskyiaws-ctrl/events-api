from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import TenantAccountNotFoundError
from app.domain.schemas import TenantAccount, TenantAccountCreate, TenantAccountUpdate
from app.repositories.models import TenantAccountORM


class TenantAccountRepository(ABC):
    """The port for Milestone 8's CDC source table. No tenant_id scoping
    on any of these — unlike EventRepository, this isn't a tenant-scoped
    resource, it's the tenant registry itself (see
    app/domain/exceptions.py's TenantAccountNotFoundError and the grant
    migration's docstring for why)."""

    @abstractmethod
    async def create(self, tenant_account_in: TenantAccountCreate) -> TenantAccount:
        """Persist a new tenant account. No id/created_at/updated_at
        passed in — TenantAccountORM generates all three via
        server_default (see models.py), so this method must read the
        DB-assigned values back and return the full TenantAccount,
        including them.

        This is a deliberate split from EventRepository.add, which does
        take a fully-formed domain object with id already assigned: that
        convention traces back to keeping InMemoryEventRepository (since
        retired, see app/repositories/memory.py) and PostgresEventRepository
        substitutable. There's no InMemoryTenantAccountRepository — only
        Postgres ever implemented this port — so there's nothing forcing
        id/timestamp generation up into the service the way it did for
        Event."""

    @abstractmethod
    async def update(self, tenant_id: UUID, updates: TenantAccountUpdate) -> TenantAccount:
        """Apply only the fields present in `updates` (non-None) to the
        tenant_id row, leaving the rest unchanged. Raise
        TenantAccountNotFoundError if tenant_id doesn't exist. Return the
        row as it looks after the update."""


class PostgresTenantAccountRepository(TenantAccountRepository):
    """The real adapter — same shape as PostgresEventRepository, just for
    the tenant registry.

    create(tenant_account_in) needs no id/created_at/updated_at built
    client-side: server_default handles all three on INSERT (id via
    gen_random_uuid(), timestamps via now()). The refresh() after commit()
    is technically redundant here — SQLAlchemy's eager_defaults ("auto",
    the 2.0 default) fetches server_default values via RETURNING as part
    of the INSERT itself — but it's kept for symmetry with update() below,
    where the equivalent auto-fetch does NOT happen and refresh() is load-
    bearing, not just explicit.

    update(tenant_id, updates)'s refresh() before returning is required,
    not optional: confirmed empirically against a real Postgres instance
    that an UPDATE's onupdate-generated value is NOT eagerly fetched back
    onto the ORM row the way an INSERT's server_default is. Reading
    row.updated_at without refreshing first triggers a lazy reload outside
    of an awaited context, which raises sqlalchemy.exc.MissingGreenlet —
    this is why the naive version of this method looked fine locally
    (INSERT path) but broke the first time PATCH was actually exercised.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_account_in: TenantAccountCreate) -> TenantAccount:
        row = TenantAccountORM(**tenant_account_in.model_dump())
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)

        return TenantAccount.model_validate(row, from_attributes=True)

    async def update(self, tenant_id: UUID, updates: TenantAccountUpdate) -> TenantAccount:
        stmt = select(TenantAccountORM).where(TenantAccountORM.id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            raise TenantAccountNotFoundError(tenant_id)

        for field, value in updates.model_dump(exclude_none=True).items():
            setattr(row, field, value)

        await self._session.commit()
        await self._session.refresh(row)

        return TenantAccount.model_validate(row, from_attributes=True)
