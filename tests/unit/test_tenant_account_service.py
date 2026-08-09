"""Unit tests for TenantAccountService. Same idea as test_event_service.py
— mock_tenant_account_repository (see tests/conftest.py) stands in for
TenantAccountRepository, so only TenantAccountService's own logic is under
test: the blank-name validation, and that create/update are otherwise
pure passthroughs to the repository (id/timestamps are DB-generated, not
assigned here — see TenantAccountService's docstring).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.exceptions import DomainError
from app.domain.schemas import TenantAccount, TenantAccountCreate, TenantAccountUpdate
from app.services.tenant_accounts import TenantAccountService

TENANT_ID = uuid4()


async def test_create_delegates_to_repository(mock_tenant_account_repository):
    service = TenantAccountService(mock_tenant_account_repository)

    tenant_account_in = TenantAccountCreate(name="Acme", plan_tier="free")
    expected = mock_tenant_account_repository.create.return_value = TenantAccount(
        id=TENANT_ID,
        name="Acme",
        plan_tier="free",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    result = await service.create(tenant_account_in)

    mock_tenant_account_repository.create.assert_awaited_once_with(tenant_account_in)
    assert result == expected


async def test_create_rejects_blank_name(mock_tenant_account_repository):
    service = TenantAccountService(mock_tenant_account_repository)

    with pytest.raises(DomainError) as e_info:
        await service.create(TenantAccountCreate(name="", plan_tier="free"))

    assert str(e_info.value) == "name must not be blank"

    mock_tenant_account_repository.create.assert_not_awaited()


async def test_update_delegates_to_repository(mock_tenant_account_repository):
    service = TenantAccountService(mock_tenant_account_repository)

    updates = TenantAccountUpdate(plan_tier="enterprise")
    expected = mock_tenant_account_repository.update.return_value = TenantAccount(
        id=TENANT_ID,
        name="Acme",
        plan_tier="enterprise",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    result = await service.update(TENANT_ID, updates)

    mock_tenant_account_repository.update.assert_awaited_once_with(TENANT_ID, updates)
    assert result == expected


async def test_update_rejects_blank_name(mock_tenant_account_repository):
    service = TenantAccountService(mock_tenant_account_repository)

    with pytest.raises(DomainError) as e_info:
        await service.update(TENANT_ID, TenantAccountUpdate(name=""))

    assert str(e_info.value) == "name must not be blank"

    mock_tenant_account_repository.update.assert_not_awaited()
