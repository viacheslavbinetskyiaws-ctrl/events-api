"""tenant accounts id server default gen_random_uuid

Revision ID: 408dbcd8e671
Revises: de28b802147e
Create Date: 2026-08-08 23:44:46.071786

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '408dbcd8e671'
down_revision: Union[str, Sequence[str], None] = 'de28b802147e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "tenant_accounts",
        "id",
        server_default=sa.text("gen_random_uuid()"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "tenant_accounts",
        "id",
        server_default=None,
    )
