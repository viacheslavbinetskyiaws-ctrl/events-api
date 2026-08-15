#!/usr/bin/env bash
# Idempotently sets up Debezium CDC on tenant_accounts. Run once after
# `docker compose up -d postgres kafka kafka-connect` (and after
# `alembic upgrade head`, since tenant_accounts must exist first).
#
# Not baked into docker-compose itself as a one-shot init container: this
# only ever needs to run once per fresh environment (the publication and
# connector both persist in their respective volumes across restarts),
# so a plain idempotent script is the smaller, easier-to-read solution
# than orchestrating extra container startup ordering for a one-time step.
#
# Split into two steps because publication.autocreate.mode=filtered has a
# known bug in Debezium 3.0.0.Final (DebeziumException: "No table filters
# found for filtered publication") — creating the publication ourselves
# sidesteps it entirely, and Debezium just uses whatever publication
# already exists under that name instead of trying to create one.
set -euo pipefail

PUBLICATION_NAME="dbz_tenant_accounts_publication"
CONNECTOR_NAME="tenant-accounts-connector"

echo "Ensuring publication '$PUBLICATION_NAME' exists..."
docker compose exec -T postgres psql -U events -d events -v ON_ERROR_STOP=1 -c "
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = '$PUBLICATION_NAME') THEN
        CREATE PUBLICATION $PUBLICATION_NAME FOR TABLE public.tenant_accounts;
    END IF;
END
\$\$;
"

echo "Ensuring connector '$CONNECTOR_NAME' is registered..."
if curl -sf "localhost:8083/connectors/$CONNECTOR_NAME" > /dev/null 2>&1; then
    echo "Connector already registered, skipping."
else
    curl -sf -X POST localhost:8083/connectors \
        -H "Content-Type: application/json" \
        -d '{
          "name": "'"$CONNECTOR_NAME"'",
          "config": {
            "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
            "database.hostname": "postgres",
            "database.port": "5432",
            "database.user": "events",
            "database.password": "events",
            "database.dbname": "events",
            "topic.prefix": "cdc",
            "table.include.list": "public.tenant_accounts",
            "plugin.name": "pgoutput",
            "slot.name": "debezium_tenant_accounts",
            "publication.name": "'"$PUBLICATION_NAME"'",
            "publication.autocreate.mode": "filtered",
            "key.converter": "org.apache.kafka.connect.json.JsonConverter",
            "key.converter.schemas.enable": "false",
            "value.converter": "org.apache.kafka.connect.json.JsonConverter",
            "value.converter.schemas.enable": "false"
          }
        }'
    echo
fi

echo "Status:"
curl -s "localhost:8083/connectors/$CONNECTOR_NAME/status"
echo
