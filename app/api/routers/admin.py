from uuid import UUID

from fastapi import APIRouter

from app.api.deps import TenantAccountServiceDep
from app.domain.schemas import TenantAccount, TenantAccountCreate, TenantAccountUpdate

# No X-Tenant-ID anywhere in this router, unlike events.py/analytics.py —
# these endpoints manage tenants, they aren't scoped to one (see
# TenantAccountRepository's docstring for why that's a different access
# pattern, not an oversight).
router = APIRouter(prefix="/admin/tenants", tags=["admin"])


@router.post("", response_model=TenantAccount, status_code=201)
async def create_tenant_account(
    tenant_in: TenantAccountCreate, service: TenantAccountServiceDep
) -> TenantAccount:
    return await service.create(tenant_in)


@router.patch("/{tenant_id}", response_model=TenantAccount)
async def update_tenant_account(
    tenant_id: UUID, updates: TenantAccountUpdate, service: TenantAccountServiceDep
) -> TenantAccount:
    return await service.update(tenant_id, updates)
