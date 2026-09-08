"""Reads the dbt artifacts this same CronJob run just produced
(target/run_results.json, target/sources.json) and upserts the merged
result into data_quality_runs — the one row /health/data-quality reads.

Deliberately standalone, not reusing app.services.data_quality's merge
logic: importing from app/ would mean this image also needs the API's
entire dependency tree (fastapi, sqlalchemy, pydantic-settings, ...) just
to reuse a small parsing routine. Same "each component keeps its own
narrow logic" reasoning streaming/config.py already documents for not
reusing app.core.config.Settings.

Run from dbt/ (matches runtime-dbt's WORKDIR), after `dbt build` and
`dbt source freshness` have both already run in this same container.
psycopg2 needs no separate install — it's already a transitive
dependency of dbt-postgres.
"""

import json
import os
from pathlib import Path

import boto3
import psycopg2

# Deliberate duplicate of app/services/data_quality.py's
# _PASSING_STATUSES/_freshness_check (see this module's own docstring for
# why it's not imported instead) — "success" for models/snapshots, "pass"
# for tests and for freshness. If either copy's vocabulary or message
# format changes, check the other; nothing enforces they stay in sync.
_PASSING_STATUSES = {"success", "pass"}


def _load_run_results() -> dict:
    return json.loads(Path("target/run_results.json").read_text())


def _load_freshness() -> dict:
    return json.loads(Path("target/sources.json").read_text())


def _freshness_message(result: dict) -> str | None:
    if result["status"] in _PASSING_STATUSES:
        return None

    criteria = result.get("criteria") or {}
    threshold = (
        criteria.get("error_after") if result["status"] == "error" else criteria.get("warn_after")
    )
    threshold_desc = f"{threshold['count']} {threshold['period']}" if threshold else "unset"
    age = result.get("max_loaded_at_time_ago_in_s")
    age_desc = f"{age:.0f}s" if age is not None else "unknown"
    return f"stale: last loaded {age_desc} ago, threshold {threshold_desc}"


def build_report() -> tuple[str, bool, list[dict]]:
    run_results = _load_run_results()
    freshness = _load_freshness()

    checks = [
        {"unique_id": r["unique_id"], "status": r["status"], "message": r.get("message")}
        for r in run_results["results"]
    ] + [
        {"unique_id": r["unique_id"], "status": r["status"], "message": _freshness_message(r)}
        for r in freshness["results"]
    ]

    # ISO 8601 UTC timestamps sort correctly as plain strings — no need
    # to parse into datetime objects just to compare them.

    generated_at = min(
        run_results["metadata"]["generated_at"], freshness["metadata"]["generated_at"]
    )
    passed = all(check["status"] in _PASSING_STATUSES for check in checks)

    return generated_at, passed, checks


def main() -> None:
    generated_at, passed, checks = build_report()

    conn = psycopg2.connect(
        host=os.environ.get("DBT_HOST", "localhost"),
        port=os.environ.get("DBT_PORT", "5432"),
        user=os.environ.get("DBT_USER", "events"),
        password=os.environ.get("DBT_PASSWORD", "events"),
        dbname=os.environ.get("DBT_DBNAME", "events"),
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_quality_runs (id, generated_at, passed, checks, updated_at)
                VALUES (1, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    generated_at = EXCLUDED.generated_at,
                    passed = EXCLUDED.passed,
                    checks = EXCLUDED.checks,
                    updated_at = now()
                """,
                (generated_at, passed, json.dumps(checks)),
            )
    finally:
        conn.close()

    print(f"data_quality_runs updated: passed={passed}, {len(checks)} checks")

    # Second destination for the same passed boolean — a genuinely different
    # signal type (continuous Prometheus-style metrics don't fit a one-shot
    # batch job's exit status naturally) gets a genuinely different tool.
    if os.environ.get("APP_ENVIRONMENT") == "aws":
        cloudwatch = boto3.client("cloudwatch", region_name=os.environ["APP_AWS_REGION"])
        cloudwatch.put_metric_data(
            Namespace="EventsApi/DataQuality",
            MetricData=[
                {
                    "MetricName": "DbtBuildPassed",
                    "Value": 1.0 if passed else 0.0,
                    "Unit": "None",
                }
            ],
        )
        print(f"CloudWatch metric pushed: DbtBuildPassed={1.0 if passed else 0.0}")


if __name__ == "__main__":
    main()
