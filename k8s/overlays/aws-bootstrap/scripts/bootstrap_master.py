"""Bootstrap Job 1: the one step that cannot use IAM auth on a truly fresh
RDS instance.

A freshly created RDS instance's owner role (`events`) can only authenticate
with the RDS-managed master password until `GRANT rds_iam TO events` has run
once. But this Job is deleted and recreated, unconditionally, on every
`cluster-up` (idempotent re-runs, retries after an unrelated failure, etc.) —
and once the grant has landed, AWS makes IAM authentication mandatory for
that role: a later attempt with the master password is then rejected
outright ("IAM authentication takes precedence over password authentication
... the user has to log in as an IAM user", confirmed live 2026-09-22:
`InvalidAuthorizationSpecificationError: PAM authentication failed for user
"events"` on a re-run against an RDS instance a prior run had already
granted). So the two auth modes are mutually exclusive on this one role, and
which one currently works depends on state this script doesn't otherwise
know.

Made idempotent by trying IAM auth first: if `events` has no grant yet, that
raises the specific, distinguishable `InvalidPasswordError` (SQLSTATE
28P01, a subclass of the broader authorization error above — confirmed
against this project's own pinned asyncpg==0.31.0), which is exactly the
signal to fall back to the master password. If the grant already exists, the
IAM attempt just succeeds and the GRANT below is a safe no-op (Postgres
issues a NOTICE, not an error, for a membership that already exists).

The master password, read from Secrets Manager (IRSA-scoped to that one
secret), lives only in this process's memory: never in a file, a log line or
a Kubernetes Secret.
"""

import asyncio
import json
import os

import asyncpg
import boto3


async def _connect_via_iam(host: str, region: str) -> asyncpg.Connection:
    rds = boto3.client("rds", region_name=region)
    token = rds.generate_db_auth_token(
        DBHostname=host, Port=5432, DBUsername="events", Region=region
    )
    return await asyncpg.connect(
        host=host,
        port=5432,
        user="events",
        password=token,
        database="events",
        ssl="require",
    )


async def _connect_via_master_password(
    host: str, region: str, secret_arn: str
) -> asyncpg.Connection:
    secrets = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(secrets.get_secret_value(SecretId=secret_arn)["SecretString"])
    return await asyncpg.connect(
        host=host,
        port=5432,
        user=secret["username"],
        password=secret["password"],
        database="events",
        ssl="require",
    )


async def connect_idempotently(iam_connect, password_connect) -> asyncpg.Connection:
    """Connect as `events` regardless of whether rds_iam has already been
    granted in a prior run. `iam_connect`/`password_connect` are zero-arg
    async callables — injected so this branching is testable without a real
    database; main() binds them to the real host/region/secret via closures."""
    try:
        conn = await iam_connect()
        print("events already has rds_iam; connected via IAM")
        return conn
    except asyncpg.InvalidPasswordError:
        conn = await password_connect()
        print("events has no rds_iam yet; connected via the master password")
        return conn


async def main() -> None:
    host = os.environ["RDS_HOST"]
    region = os.environ["AWS_REGION"]
    secret_arn = os.environ["RDS_MASTER_SECRET_ARN"]

    conn = await connect_idempotently(
        lambda: _connect_via_iam(host, region),
        lambda: _connect_via_master_password(host, region, secret_arn),
    )
    try:
        await conn.execute("GRANT rds_iam TO events")
        print("granted rds_iam to events")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
