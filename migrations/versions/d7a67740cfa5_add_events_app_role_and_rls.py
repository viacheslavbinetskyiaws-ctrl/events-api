"""add events_app role and enable RLS on events

Revision ID: d7a67740cfa5
Revises: 26e3e99a90fe
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd7a67740cfa5'
down_revision: Union[str, Sequence[str], None] = '26e3e99a90fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dev-only credential, same convention already used throughout this project
# (docker-compose's POSTGRES_PASSWORD, dbt/profiles.yml, k8s secrets) — not
# a real secret, this is a local learning environment.
_APP_ROLE = "events_app"
_APP_ROLE_PASSWORD = "events_app"  # noqa: S105


def upgrade() -> None:
    """Upgrade schema."""
    # Roles are cluster-wide, not per-database — this migration also runs
    # against events_test (see tests/conftest.py's ALEMBIC_DATABASE_URL
    # override), where the role will already exist from the events run.
    # CREATE ROLE has no IF NOT EXISTS, hence the guard.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                CREATE ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_ROLE_PASSWORD}';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT CONNECT ON DATABASE events TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON events TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT ON daily_event_counts TO {_APP_ROLE}")

    # `events` (the table owner, used by Alembic/dbt) is a Postgres
    # superuser via the POSTGRES_USER bootstrap env var — superusers bypass
    # RLS unconditionally, regardless of ownership or FORCE ROW LEVEL
    # SECURITY. events_app is a plain, non-owner login role, so this policy
    # applies to it automatically with no FORCE needed. This is what keeps
    # dbt's cross-tenant mart aggregation working while the app itself gets
    # enforced isolation.
    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON events
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )
    # current_setting(..., true) returns NULL instead of raising when unset
    # (missing_ok=true) — so a connection that never sets app.current_tenant
    # sees zero rows (tenant_id = NULL is never true), not an error and not
    # every tenant's data. Fail-closed by default.


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON events")
    op.execute("ALTER TABLE events DISABLE ROW LEVEL SECURITY")
    op.execute(f"REVOKE ALL PRIVILEGES ON daily_event_counts FROM {_APP_ROLE}")
    op.execute(f"REVOKE ALL PRIVILEGES ON events FROM {_APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE}")
    op.execute(f"REVOKE CONNECT ON DATABASE events FROM {_APP_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {_APP_ROLE}")
