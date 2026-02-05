import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, Query
from psycopg.rows import dict_row


app = FastAPI(title="shipping-service", version="0.1.0")


def get_conn() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "ecomm"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
        row_factory=dict_row,
        autocommit=True,
    )


def bootstrap() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS shipping;
            CREATE TABLE IF NOT EXISTS shipping.shipment (
              shipment_id text PRIMARY KEY,
              order_id text NOT NULL UNIQUE,
              status text NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS shipping.outbox_event (
              id uuid PRIMARY KEY,
              aggregate_type text NOT NULL,
              aggregate_id text NOT NULL,
              event_type text NOT NULL,
              payload jsonb NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now()
            );
            """
        )


def emit_outbox(cur: psycopg.Cursor, aggregate_id: str, payload: dict[str, Any]) -> None:
    event_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO shipping.outbox_event (id, aggregate_type, aggregate_id, event_type, payload, created_at)
        VALUES (%(id)s, 'shipping.ShipmentCreated.v1', %(aggregate_id)s, 'ShipmentCreated', %(payload)s::jsonb, now())
        """,
        {
            "id": event_id,
            "aggregate_id": aggregate_id,
            "payload": json.dumps(
                {
                    "event_id": str(event_id),
                    "event_type": "ShipmentCreated",
                    "event_version": 1,
                    "producer": "shipping-service",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "key": {"order_id": aggregate_id},
                    "data": payload,
                }
            ),
        },
    )


@app.on_event("startup")
def on_startup() -> None:
    bootstrap()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "shipping"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "shipping"}


@app.get("/api/v1/shipments")
def get_shipment(order_id: str = Query(..., min_length=3)) -> dict[str, Any] | list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT shipment_id, order_id, status, created_at, updated_at
            FROM shipping.shipment
            WHERE order_id = %(order_id)s
            """,
            {"order_id": order_id},
        )
        shipment = cur.fetchone()

        if not shipment:
            # Convenience for test flow: create shipment when order is PAID.
            cur.execute("SELECT to_regclass('orders.\"order\"') AS table_name")
            has_orders_table = cur.fetchone()
            order = None
            if has_orders_table and has_orders_table["table_name"]:
                cur.execute(
                    """
                    SELECT status
                    FROM orders."order"
                    WHERE order_id = %(order_id)s
                    """,
                    {"order_id": order_id},
                )
                order = cur.fetchone()
            if order and order["status"] == "PAID":
                shipment_id = f"ship-{uuid.uuid4().hex[:8]}"
                cur.execute(
                    """
                    INSERT INTO shipping.shipment (shipment_id, order_id, status)
                    VALUES (%(shipment_id)s, %(order_id)s, 'CREATED')
                    RETURNING shipment_id, order_id, status, created_at, updated_at
                    """,
                    {"shipment_id": shipment_id, "order_id": order_id},
                )
                shipment = cur.fetchone()
                emit_outbox(
                    cur,
                    aggregate_id=order_id,
                    payload={"shipment_id": shipment_id, "order_id": order_id, "status": "CREATED"},
                )

        if not shipment:
            return []

        shipment["created_at"] = shipment["created_at"].isoformat()
        shipment["updated_at"] = shipment["updated_at"].isoformat()
        return shipment
