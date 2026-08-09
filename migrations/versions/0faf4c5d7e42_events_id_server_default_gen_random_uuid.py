"""events id server default gen_random_uuid

Revision ID: 0faf4c5d7e42
Revises: 408dbcd8e671
Create Date: 2026-08-09 01:40:50.073285

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0faf4c5d7e42'
down_revision: Union[str, Sequence[str], None] = '408dbcd8e671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "events",
        "id",
        server_default=sa.text("gen_random_uuid()"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "events",
        "id",
        server_default=None,
    )
