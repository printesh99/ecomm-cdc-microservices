# ecomm-cdc-microservices

E-commerce CDC demo for OpenShift:
- Backend microservices: FastAPI
- Database: PostgreSQL HA on Patroni
- CDC: Debezium PostgreSQL connector (Kafka Connect / Strimzi)
- Messaging: Kafka
- GitOps: Argo CD
- CI: Jenkins

## Which Postgres setup fits best?

For **modern cloud-native microservices in production**, operator-managed PostgreSQL (for example CloudNativePG/Crunchy) is usually best.

For **your current learning and testing flow on CRC/OpenShift**, staying with **Patroni** is a good choice because your stack is already running and integrated with Debezium.

## Repository layout

- `services/catalog` - Catalog APIs (`GET /api/v1/products`, `GET /api/v1/products/{id}`)
- `services/cart` - Cart APIs (`POST/GET/PATCH/DELETE` cart endpoints)
- `services/orders` - Order APIs (`POST /api/v1/orders`, `GET /api/v1/orders/{id}`)
- `services/payment` - Payment APIs (`POST /api/v1/payments`, `POST /api/v1/payments/{id}/confirm`)
- `services/shipping` - Shipment API (`GET /api/v1/shipments?order_id=...`)
- `schemas/events/v1` - JSON schemas for v1 events
- `docs` - architecture and operational notes
- `Jenkinsfile` - OpenShift binary builds + GitOps trigger pipeline

## Single docker compose (recommended)

From repo root:

```bash
docker compose up -d --build
./scripts/register_connector.sh
./scripts/local_smoke_order.sh
```

What this gives you:
- FastAPI services on `localhost:8001..8005`
- PostgreSQL on `localhost:5432`
- Kafka broker on `localhost:29092`
- Kafka Connect REST on `localhost:8083`
- Kafka UI on `http://localhost:8088`

To stop:

```bash
docker compose down -v
```

## Quick local run (manual per service)

```bash
cd services/catalog
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PGHOST=localhost PGPORT=5432 PGDATABASE=ecomm PGUSER=postgres PGPASSWORD=postgres
uvicorn app:app --reload --port 8001
```

Use similar commands for other services on different ports.

## Notes

- Services create missing schemas/tables at startup for easier local testing.
- Each service exposes `/health` and `/ready`.
- Outbox rows are inserted into schema-specific `outbox_event` tables to support Debezium outbox routing.
- Connector config for local CDC outbox routing is in `docker/connectors/ecomm-outbox.json`.
