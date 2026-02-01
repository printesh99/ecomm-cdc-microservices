# Architecture (v1)

This project implements an e-commerce microservices demo with:
- FastAPI microservices
- PostgreSQL (Patroni) as system of record (single DB `ecomm`, schemas per service)
- Transactional Outbox per service schema
- Debezium (Kafka Connect) reading PostgreSQL WAL logical decoding via replication slots
- Kafka for event streaming between services
- Jenkins + Argo CD (GitOps) for CI/CD
- Prometheus + Grafana for observability

Paste your FigJam links and screenshots under `docs/`.
