"""Bootstrap Job 2: post-migration role setup, over IAM as the owner role.

1. GRANT rds_iam TO events_app (the migration created the role; the API pods
   authenticate as it over IAM).
2. Make the debezium_replication role's password match the canonical value in
   Secrets Manager, generating and storing one on the very first run.
   Debezium's replication connection cannot use IAM auth (AWS: "you can't use
   IAM authentication to establish a replication connection"), so this role
   must stay password-authenticated. The value is never printed.
3. Drop any replication slot RDS has already marked `lost`. A lost slot never
   self-heals (confirmed live 2026-09-22: an inactive slot that sits idle
   while unrelated WAL keeps flowing past max_slot_wal_keep_size gets
   invalidated by RDS, permanently), and Debezium's own attempt to resume
   from one crash-loops forever ("Unable to obtain valid replication slot")
   rather than recovering on its own. This runs on every `up`, before the
   connectors are (re-)applied, so cluster-up self-heals from this on its
   own — no manual runbook, regardless of what caused the slot to go bad.

All three steps are safe to repeat on every `up`.
"""

import asyncio
import os

import asyncpg
import boto3

DEBEZIUM_SECRET_ID = "events-api/debezium-replication"
DEBEZIUM_ROLE = "debezium_replication"


def canonical_debezium_password(secrets) -> str:
    """Return the stored password, generating and storing one if the secret
    container is still empty (VersionIdsToStages is absent until the first
    PutSecretValue)."""
    described = secrets.describe_secret(SecretId=DEBEZIUM_SECRET_ID)
    if not described.get("VersionIdsToStages"):
        generated = secrets.get_random_password(PasswordLength=32, ExcludePunctuation=True)[
            "RandomPassword"
        ]
        secrets.put_secret_value(SecretId=DEBEZIUM_SECRET_ID, SecretString=generated)
        print("stored a newly generated Debezium password")
        return generated
    return secrets.get_secret_value(SecretId=DEBEZIUM_SECRET_ID)["SecretString"]


async def clear_lost_replication_slots(conn) -> None:
    """Drop every slot RDS has marked wal_status='lost' — a state that never
    recovers on its own. Matches by slot state, not by name, so it also
    covers any replication slot added later, not just today's two."""
    rows = await conn.fetch("SELECT slot_name FROM pg_replication_slots WHERE wal_status = 'lost'")
    for row in rows:
        await conn.execute("SELECT pg_drop_replication_slot($1)", row["slot_name"])
        print(f"dropped lost replication slot {row['slot_name']}")


async def main() -> None:
    host = os.environ["RDS_HOST"]
    region = os.environ["AWS_REGION"]

    rds = boto3.client("rds", region_name=region)
    secrets = boto3.client("secretsmanager", region_name=region)

    token = rds.generate_db_auth_token(
        DBHostname=host, Port=5432, DBUsername="events", Region=region
    )
    password = canonical_debezium_password(secrets)

    conn = await asyncpg.connect(
        host=host,
        port=5432,
        user="events",
        password=token,
        database="events",
        ssl="require",
    )
    try:
        await conn.execute("GRANT rds_iam TO events_app")
        # Let the server quote the identifier and literal: no string
        # interpolation of the password into SQL on our side.
        alter = await conn.fetchval(
            "SELECT format('ALTER ROLE %I WITH PASSWORD %L', $1::text, $2::text)",
            DEBEZIUM_ROLE,
            password,
        )
        await conn.execute(alter)
        print("granted rds_iam to events_app; synced the debezium_replication password")
        await clear_lost_replication_slots(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
