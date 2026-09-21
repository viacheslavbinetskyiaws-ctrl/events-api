"""Bootstrap Job 1: the one step that cannot use IAM auth.

A freshly created RDS instance's owner role (`events`) can only authenticate
with the RDS-managed master password until `GRANT rds_iam TO events` has run
(observed live: IAM auth for a role with no rds_iam grant fails with a plain
password-auth error). This script reads that password from Secrets Manager
(IRSA-scoped to that one secret), connects with it once, grants rds_iam and
exits. Every later Job authenticates over IAM. The password lives only in this
process's memory: never in a file, a log line or a Kubernetes Secret.

Re-running is safe: granting a role membership that already exists is a
NOTICE in Postgres, not an error.
"""

import asyncio
import json
import os

import asyncpg
import boto3


async def main() -> None:
    host = os.environ["RDS_HOST"]
    region = os.environ["AWS_REGION"]
    secret_arn = os.environ["RDS_MASTER_SECRET_ARN"]

    secrets = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(secrets.get_secret_value(SecretId=secret_arn)["SecretString"])

    conn = await asyncpg.connect(
        host=host,
        port=5432,
        user=secret["username"],
        password=secret["password"],
        database="events",
        ssl="require",
    )
    try:
        await conn.execute("GRANT rds_iam TO events")
        print("granted rds_iam to events")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
