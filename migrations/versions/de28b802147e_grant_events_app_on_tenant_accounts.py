"""grant events_app on tenant_accounts

Revision ID: de28b802147e
Revises: 8c5cc5c8a33d
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'de28b802147e'
down_revision: Union[str, Sequence[str], None] = '8c5cc5c8a33d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# No RLS here, unlike events/daily_event_counts: tenant_accounts is the
# tenant registry itself, written by an admin operation with no existing
# X-Tenant-ID context to scope against (you can't scope "create a tenant"
# by the tenant being created). events_app still needs real grants though —
# it's the role the app's admin endpoint will actually write through.
_APP_ROLE = "events_app"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenant_accounts TO {_APP_ROLE}")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON tenant_accounts FROM {_APP_ROLE}")
