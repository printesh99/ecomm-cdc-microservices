# ecomm-cdc-microservices

Portfolio project: E-commerce microservices (Python/FastAPI) using PostgreSQL (Patroni) + Debezium CDC (Kafka Connect) + Kafka events.

## Contents
- `docs/` : architecture + event contracts
- `schemas/events/v1/` : JSON Schemas for Kafka event payloads (v1)

## Event Schemas
All v1 events share a common envelope schema:
- `schemas/events/v1/envelope.schema.json`

Per-event schemas live alongside it (same folder). They reference the envelope using a relative `$ref`.

> Generated on 2026-02-01
