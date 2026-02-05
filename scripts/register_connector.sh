#!/usr/bin/env bash
set -euo pipefail

CONNECT_URL="${CONNECT_URL:-http://localhost:8083}"
CONFIG_FILE="${CONFIG_FILE:-./docker/connectors/ecomm-outbox.json}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Connector config not found: $CONFIG_FILE"
  exit 1
fi

echo "Waiting for Kafka Connect at $CONNECT_URL ..."
for _ in {1..60}; do
  if curl -fsS "$CONNECT_URL/connectors" >/dev/null; then
    break
  fi
  sleep 2
done

if ! curl -fsS "$CONNECT_URL/connectors" >/dev/null; then
  echo "Kafka Connect is not reachable."
  exit 1
fi

echo "Recreating connector ecomm-outbox ..."
curl -fsS -X DELETE "$CONNECT_URL/connectors/ecomm-outbox" >/dev/null || true
curl -fsS -X POST "$CONNECT_URL/connectors" \
  -H 'Content-Type: application/json' \
  --data @"$CONFIG_FILE" >/dev/null

echo "Connector created. Current status:"
curl -fsS "$CONNECT_URL/connectors/ecomm-outbox/status" | python3 -m json.tool
