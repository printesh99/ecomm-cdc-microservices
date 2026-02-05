import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row


app = FastAPI(title="orders-service", version="0.1.0")


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


class OrderItemIn(BaseModel):
    product_id: str
    qty: int = Field(ge=1)
    unit_price: float = Field(gt=0)


class CreateOrderRequest(BaseModel):
    customer_id: str
    cart_id: str
    items: list[OrderItemIn]
    currency: str = "USD"


def bootstrap() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS orders;
            CREATE TABLE IF NOT EXISTS orders."order" (
              order_id text PRIMARY KEY,
              customer_id text NOT NULL,
              status text NOT NULL,
              total_amount numeric(12,2) NOT NULL,
              currency text NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS orders.order_item (
              order_id text NOT NULL REFERENCES orders."order"(order_id) ON DELETE CASCADE,
              product_id text NOT NULL,
              qty integer NOT NULL CHECK (qty > 0),
              unit_price numeric(12,2) NOT NULL,
              line_total numeric(12,2) NOT NULL,
              PRIMARY KEY (order_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS orders.outbox_event (
              id uuid PRIMARY KEY,
              aggregate_type text NOT NULL,
              aggregate_id text NOT NULL,
              event_type text NOT NULL,
              payload jsonb NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now()
            );
            """
        )


def emit_outbox(cur: psycopg.Cursor, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> None:
    event_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO orders.outbox_event (id, aggregate_type, aggregate_id, event_type, payload, created_at)
        VALUES (%(id)s, %(aggregate_type)s, %(aggregate_id)s, %(event_type)s, %(payload)s::jsonb, now())
        """,
        {
            "id": event_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "event_type": event_type,
            "payload": json.dumps(
                {
                    "event_id": str(event_id),
                    "event_type": event_type,
                    "event_version": 1,
                    "producer": "orders-service",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "key": {"order_id": aggregate_id},
                    "data": payload,
                }
            ),
        },
    )


def get_order(cur: psycopg.Cursor, order_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT order_id, customer_id, status, total_amount, currency, created_at, updated_at
        FROM orders."order"
        WHERE order_id = %(order_id)s
        """,
        {"order_id": order_id},
    )
    order = cur.fetchone()
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    cur.execute(
        """
        SELECT product_id, qty, unit_price, line_total
        FROM orders.order_item
        WHERE order_id = %(order_id)s
        ORDER BY product_id
        """,
        {"order_id": order_id},
    )
    items = cur.fetchall()
    for it in items:
        it["unit_price"] = float(it["unit_price"])
        it["line_total"] = float(it["line_total"])

    order["total_amount"] = float(order["total_amount"])
    order["created_at"] = order["created_at"].isoformat()
    order["updated_at"] = order["updated_at"].isoformat()
    order["items"] = items
    return order


@app.on_event("startup")
def on_startup() -> None:
    bootstrap()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "orders"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "orders"}


@app.post("/api/v1/orders")
def create_order(payload: CreateOrderRequest) -> dict[str, str]:
    if not payload.items:
        raise HTTPException(status_code=400, detail="order requires at least one item")

    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    total = round(sum(i.qty * i.unit_price for i in payload.items), 2)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders."order" (order_id, customer_id, status, total_amount, currency)
            VALUES (%(order_id)s, %(customer_id)s, 'PENDING_PAYMENT', %(total_amount)s, %(currency)s)
            """,
            {
                "order_id": order_id,
                "customer_id": payload.customer_id,
                "total_amount": total,
                "currency": payload.currency,
            },
        )

        for it in payload.items:
            line_total = round(it.qty * it.unit_price, 2)
            cur.execute(
                """
                INSERT INTO orders.order_item (order_id, product_id, qty, unit_price, line_total)
                VALUES (%(order_id)s, %(product_id)s, %(qty)s, %(unit_price)s, %(line_total)s)
                """,
                {
                    "order_id": order_id,
                    "product_id": it.product_id,
                    "qty": it.qty,
                    "unit_price": it.unit_price,
                    "line_total": line_total,
                },
            )

        # Mark cart as checked out when cart table exists.
        cur.execute("SELECT to_regclass('cart.cart') AS table_name")
        has_cart_table = cur.fetchone()
        if has_cart_table and has_cart_table["table_name"]:
            cur.execute(
                """
                UPDATE cart.cart
                SET status = 'CHECKED_OUT', updated_at = now()
                WHERE cart_id = %(cart_id)s
                """,
                {"cart_id": payload.cart_id},
            )

        emit_outbox(
            cur,
            aggregate_type="orders.OrderCreated.v1",
            aggregate_id=order_id,
            event_type="OrderCreated",
            payload={
                "order_id": order_id,
                "customer_id": payload.customer_id,
                "status": "PENDING_PAYMENT",
                "currency": payload.currency,
                "total_amount": total,
                "items": [item.model_dump() for item in payload.items],
            },
        )

    return {"order_id": order_id, "status": "PENDING_PAYMENT"}


@app.get("/api/v1/orders/{order_id}")
def get_order_api(order_id: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        return get_order(cur, order_id)
