"""add data quality runs table

Revision ID: dd949d031fce
Revises: 58537738edb7
Create Date: 2026-08-24 17:58:57.029639

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'dd949d031fce'
down_revision: Union[str, Sequence[str], None] = '58537738edb7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "events_app"


def upgrade() -> None:
    """Upgrade schema."""
    # Singleton table — id is always 1, enforced by the CHECK constraint.
    # /health/data-quality only ever needs "what's the current state," not
    # a history of past runs, so this is deliberately not append-only —
    # the CronJob upserts this one row on every run instead of inserting
    # a new one each time.
    op.create_table(
        'data_quality_runs',
        sa.Column('id', sa.SmallInteger(), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('id = 1', name='data_quality_runs_singleton'),
    )
    op.execute(f"GRANT SELECT ON data_quality_runs TO {_APP_ROLE}")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"REVOKE SELECT ON data_quality_runs FROM {_APP_ROLE}")
    op.drop_table('data_quality_runs')
