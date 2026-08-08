"""add tenant_id to events

Revision ID: 26e3e99a90fe
Revises: f66b6ca59996
Create Date: 2026-08-07 00:11:19.885166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '26e3e99a90fe'
down_revision: Union[str, Sequence[str], None] = 'f66b6ca59996'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Backfill target for any pre-existing rows (none exist right now, but this
# is written as the deliberate nullable -> backfill -> NOT NULL sequence
# rather than skipping straight to a hard NOT NULL add).
_DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Upgrade schema."""
    # NOTE: autogenerate also proposed `op.drop_table('daily_event_counts')`
    # here — a false positive. daily_event_counts is dbt-owned (DBTBase,
    # not Base) and deliberately excluded from Alembic's target_metadata in
    # migrations/env.py, so autogenerate sees it as an orphan table and
    # wants to drop it. Left out entirely; dbt owns that table's DDL.
    op.add_column('events', sa.Column('tenant_id', sa.Uuid(), nullable=True))
    op.execute(
        sa.text("UPDATE events SET tenant_id = :tenant_id WHERE tenant_id IS NULL")
        .bindparams(sa.bindparam("tenant_id", value=_DEFAULT_TENANT_ID, type_=sa.Uuid()))
    )
    op.alter_column('events', 'tenant_id', nullable=False)
    op.drop_index(op.f('ix_events_occurred_at_desc'), table_name='events')
    op.create_index('ix_events_tenant_occurred_at', 'events', ['tenant_id', 'occurred_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_events_tenant_occurred_at', table_name='events')
    op.create_index(op.f('ix_events_occurred_at_desc'), 'events', ['occurred_at'], unique=False)
    op.drop_column('events', 'tenant_id')
