#!/usr/bin/env bash
set -euo pipefail

json_get() {
  local key="$1"
  python3 - "$key" <<'PY'
import json, sys
key = sys.argv[1]
obj = json.load(sys.stdin)
print(obj[key])
PY
}

echo "Creating cart ..."
CART_JSON=$(curl -fsS -X POST http://localhost:8002/api/v1/carts \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"cust-1"}')
CART_ID=$(printf '%s' "$CART_JSON" | json_get cart_id)
echo "Cart: $CART_ID"

echo "Adding cart item ..."
curl -fsS -X POST "http://localhost:8002/api/v1/carts/$CART_ID/items" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"sku-1","qty":1}' >/dev/null

echo "Creating order ..."
ORDER_JSON=$(curl -fsS -X POST http://localhost:8003/api/v1/orders \
  -H 'Content-Type: application/json' \
  -d "{\"customer_id\":\"cust-1\",\"cart_id\":\"$CART_ID\",\"items\":[{\"product_id\":\"sku-1\",\"qty\":1,\"unit_price\":50}],\"currency\":\"USD\"}")
ORDER_ID=$(printf '%s' "$ORDER_JSON" | json_get order_id)
echo "Order: $ORDER_ID"

echo "Initiating payment ..."
PAY_JSON=$(curl -fsS -X POST http://localhost:8004/api/v1/payments \
  -H 'Content-Type: application/json' \
  -d "{\"order_id\":\"$ORDER_ID\",\"amount\":50,\"currency\":\"USD\",\"method\":\"CARD\"}")
PAY_ID=$(printf '%s' "$PAY_JSON" | json_get payment_id)
echo "Payment: $PAY_ID"

echo "Confirming payment ..."
curl -fsS -X POST "http://localhost:8004/api/v1/payments/$PAY_ID/confirm" \
  -H 'Content-Type: application/json' \
  -d '{"result":"AUTHORIZED"}' | python3 -m json.tool

echo "Checking shipment ..."
curl -fsS "http://localhost:8005/api/v1/shipments?order_id=$ORDER_ID" | python3 -m json.tool

echo "Done. Open Kafka UI at http://localhost:8088 and check ecomm.* topics."
