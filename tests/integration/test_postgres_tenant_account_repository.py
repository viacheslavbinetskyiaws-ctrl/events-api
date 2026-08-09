"""Integration test for PostgresTenantAccountRepository — same idea as
test_postgres_repository.py, but for the tenant registry.

Run with: docker compose up -d postgres && alembic upgrade head && \
          uv run pytest -m integration

Unlike EventRepository.add, create() takes a TenantAccountCreate, not a
fully-formed domain object — id/created_at/updated_at are all Postgres
server_defaults (see TenantAccountORM's docstring), so the round trip
these tests check is specifically that the repository reads those
DB-assigned values back correctly, not just that it doesn't raise.
"""

from uuid import uuid4

import pytest

from app.domain.exceptions import TenantAccountNotFoundError
from app.domain.schemas import TenantAccountCreate, TenantAccountUpdate
from app.repositories.tenant_accounts import PostgresTenantAccountRepository

pytestmark = pytest.mark.integration


async def test_create_returns_db_generated_id_and_timestamps(db_session):
    repo = PostgresTenantAccountRepository(db_session)

    result = await repo.create(TenantAccountCreate(name="Acme", plan_tier="free"))

    assert result.name == "Acme"
    assert result.plan_tier == "free"
    assert result.id is not None
    assert result.created_at is not None
    assert result.updated_at == result.created_at


async def test_update_changes_only_supplied_fields(db_session):
    repo = PostgresTenantAccountRepository(db_session)

    created = await repo.create(TenantAccountCreate(name="Acme", plan_tier="free"))

    updated = await repo.update(created.id, TenantAccountUpdate(plan_tier="enterprise"))

    assert updated.id == created.id
    assert updated.name == "Acme"
    assert updated.plan_tier == "enterprise"
    assert updated.created_at == created.created_at
    assert updated.updated_at > created.updated_at


async def test_update_raises_for_unknown_tenant(db_session):
    repo = PostgresTenantAccountRepository(db_session)

    with pytest.raises(TenantAccountNotFoundError):
        await repo.update(uuid4(), TenantAccountUpdate(plan_tier="enterprise"))
