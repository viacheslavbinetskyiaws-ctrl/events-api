"""add debezium_replication role

Revision ID: dfb55a7305b9
Revises: dd949d031fce
Create Date: 2026-09-10 03:08:15.535891

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'dfb55a7305b9'
down_revision: Union[str, Sequence[str], None] = 'dd949d031fce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dev-only credential for local/test — same convention as events_app's
# migration. In AWS specifically, this password gets rotated out-of-band
# via a manual ALTER ROLE against the live RDS instance right after this
# migration runs (same "one-off bootstrap, not IaC" pattern already used
# for Debezium's own credential) — never committed here.
_DEBEZIUM_ROLE = "debezium_replication"
_DEBEZIUM_ROLE_PASSWORD = "debezium_replication"  # noqa: S105


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_DEBEZIUM_ROLE}') THEN
                CREATE ROLE {_DEBEZIUM_ROLE} WITH LOGIN PASSWORD '{_DEBEZIUM_ROLE_PASSWORD}';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT CONNECT ON DATABASE events TO {_DEBEZIUM_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_DEBEZIUM_ROLE}")
    # SELECT is required for a non-owning role to read tables through a
    # publication it doesn't own, in addition to replication capability
    # itself (PostgreSQL enforces this for logical replication as of the
    # version in use here).
    op.execute(f"GRANT SELECT ON events, tenant_accounts TO {_DEBEZIUM_ROLE}")
    # Environment-agnostic replication grant: RDS restricts the native
    # REPLICATION attribute to its own rds_replication pseudo-role; plain
    # self-hosted Postgres (local/kind) has no such role and needs the
    # native attribute set directly instead. This one migration handles
    # both without needing to know which environment it's running in.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'rds_replication') THEN
                GRANT rds_replication TO {_DEBEZIUM_ROLE};
            ELSE
                ALTER ROLE {_DEBEZIUM_ROLE} WITH REPLICATION;
            END IF;
        END
        $$;
        """
    )
    # Deliberately never GRANT rds_iam to this role, on RDS or anywhere
    # else — that's exactly what silently broke Debezium's password auth
    # against the `events` role once IRSA-based IAM auth was added to it
    # for the migration/dbt Jobs. This role must stay plain-password-only
    # forever, since Debezium's long-lived replication connection has no
    # mechanism to refresh a short-lived IAM token.


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"REVOKE ALL PRIVILEGES ON events, tenant_accounts FROM {_DEBEZIUM_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_DEBEZIUM_ROLE}")
    op.execute(f"REVOKE CONNECT ON DATABASE events FROM {_DEBEZIUM_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {_DEBEZIUM_ROLE}")
