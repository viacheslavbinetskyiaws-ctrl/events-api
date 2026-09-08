#!/bin/sh
# Never aborts early: dbt build/source freshness returning non-zero is a
# legitimate, expected outcome (that's the whole point of this pipeline),
# not a reason to skip publishing the result. Captures all three exit
# codes, always runs every step, and only fails the Job at the very end
# if something genuinely did fail — so a real failure still shows up as
# a failed Job, but never at the cost of skipping the publish step.
if [ "$APP_ENVIRONMENT" = "aws" ]; then
  export DBT_PASSWORD=$(python scripts/generate_db_token.py)
fi

set +e

dbt build
build_exit=$?

dbt source freshness
freshness_exit=$?

python scripts/publish_data_quality.py
publish_exit=$?

if [ "$build_exit" -ne 0 ] || [ "$freshness_exit" -ne 0 ] || [ "$publish_exit" -ne 0 ]; then
  exit 1
fi
