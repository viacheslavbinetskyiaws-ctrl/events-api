"""grant events_app on tenant_account_changes

Revision ID: 17aea7bb92f4
Revises: b520db9ecd67
Create Date: 2026-08-16 19:19:07.252646

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '17aea7bb92f4'
down_revision: Union[str, Sequence[str], None] = 'b520db9ecd67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "events_app"

def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON tenant_account_changes TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT ON tenant_account_changes FROM {_APP_ROLE}")
