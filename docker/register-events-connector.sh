#!/usr/bin/env bash
# Idempotently sets up Debezium CDC on events (Milestone 13's Mongo
# properties projection). Run once after `docker compose up -d postgres
# kafka kafka-connect` (and after `alembic upgrade head`, since events
# must exist first). Same idempotent-script pattern and same
# publication.autocreate.mode=filtered workaround as
# register-tenant-accounts-connector.sh — see that script's header for
# why. Kept as its own script/publication/slot rather than folding into
# that one: each connector's replication slot is scoped to the tables in
# its own publication, and Debezium's own docs recommend a dedicated slot
# per connector rather than sharing one across unrelated tables.
set -euo pipefail

PUBLICATION_NAME="dbz_events_publication"
CONNECTOR_NAME="events-connector"

echo "Ensuring publication '$PUBLICATION_NAME' exists..."
docker compose exec -T postgres psql -U events -d events -v ON_ERROR_STOP=1 -c "
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = '$PUBLICATION_NAME') THEN
        CREATE PUBLICATION $PUBLICATION_NAME FOR TABLE public.events;
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
            "table.include.list": "public.events",
            "plugin.name": "pgoutput",
            "slot.name": "debezium_events",
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
