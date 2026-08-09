"""tenant accounts updated_at trigger

Revision ID: 95ac45427c05
Revises: 0faf4c5d7e42
Create Date: 2026-08-09 03:39:16.771534

SQLAlchemy's onupdate=func.now() (previously the only thing bumping
tenant_accounts.updated_at) is a client-side mechanism: it only fires
when SQLAlchemy itself builds the UPDATE statement. Confirmed live during
Milestone 8 CDC testing — a plain `UPDATE tenant_accounts SET ...` via
psql left updated_at completely unchanged, which a CDC/audit pipeline
built on "react to changes from any writer" cannot tolerate silently
producing a wrong timestamp. A trigger fires for every writer
unconditionally: the ORM, raw SQL, a future service, a migration
backfill — Postgres itself enforces it, no application code involved.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '95ac45427c05'
down_revision: Union[str, Sequence[str], None] = '0faf4c5d7e42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_tenant_accounts_updated_at
        BEFORE UPDATE ON tenant_accounts
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_tenant_accounts_updated_at ON tenant_accounts")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
