"""add region column to tenant_accounts

Revision ID: 58537738edb7
Revises: 17aea7bb92f4
Create Date: 2026-08-16 19:46:33.391142

Milestone 10's schema-evolution exercise: applied while the Debezium
connector and streaming/consumer.py were both live against the running
stack, deliberately without ever touching TenantAccountORM in
app/repositories/models.py — the point was to see what happens when the
table drifts underneath a live CDC pipeline the app itself doesn't know
about, not to exercise a coordinated app+migration deploy.

Result, confirmed empirically 2026-08-16: the very next change event's
`after` picked up `region` automatically, with no error anywhere in the
pipeline — not the connector, not the consumer, not the JSONB column
storing it in tenant_account_changes. That's a property of this specific
stack, not CDC in general: the connector runs with
value.converter.schemas.enable=false (plain JsonConverter, no Avro/schema
wrapper) and before/after land in genuinely unstructured JSONB on our
side, so there's no schema anywhere in the chain to violate. A real
Confluent Schema Registry setup with Avro + enforced compatibility rules
would have made this a very different, much stricter question — that's
why this project deliberately doesn't build one (see FUTURE_PLAN.md
Milestone 10).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58537738edb7'
down_revision: Union[str, Sequence[str], None] = '17aea7bb92f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_accounts", sa.Column("region", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_accounts", "region")
