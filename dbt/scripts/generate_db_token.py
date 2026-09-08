"""Mints a fresh RDS IAM auth token for the dbt CronJob's connection, printed
to stdout so run_and_publish.sh can capture it into DBT_PASSWORD. One token
per run is fine here — unlike the app's long-lived connection pool, dbt
build finishes well inside the token's 15-minute lifetime.
"""

import os

import boto3


def main() -> None:
    client = boto3.client("rds", region_name=os.environ["APP_AWS_REGION"])
    token = client.generate_db_auth_token(
        DBHostname=os.environ["DBT_HOST"],
        Port=int(os.environ.get("DBT_PORT", "5432")),
        DBUsername=os.environ.get("DBT_USER", "events"),
    )
    print(token, end="")


if __name__ == "__main__":
    main()
