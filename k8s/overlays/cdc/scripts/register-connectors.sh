#!/bin/sh
set -eu

register() {
  name="$1"
  config_file="$2"
  if curl -sf "http://kafka-connect:8083/connectors/$name" > /dev/null 2>&1; then
    echo "Connector '$name' already registered, skipping."
  else
    curl -sf -X POST http://kafka-connect:8083/connectors \
      -H "Content-Type: application/json" \
      -d "@$config_file"
    echo
  fi
}

register "tenant-accounts-connector" "/scripts/tenant-accounts-connector.json"
register "events-connector" "/scripts/events-connector.json"

echo "Status:"
curl -s "http://kafka-connect:8083/connectors/tenant-accounts-connector/status"; echo
curl -s "http://kafka-connect:8083/connectors/events-connector/status"; echo
