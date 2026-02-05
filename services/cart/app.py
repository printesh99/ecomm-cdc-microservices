import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row


app = FastAPI(title="cart-service", version="0.1.0")


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


class CreateCartRequest(BaseModel):
    customer_id: str


class AddItemRequest(BaseModel):
    product_id: str
    qty: int = Field(default=1, ge=1)


class UpdateItemRequest(BaseModel):
    qty: int = Field(ge=1)


def bootstrap() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS cart;
            CREATE TABLE IF NOT EXISTS cart.cart (
              cart_id text PRIMARY KEY,
              customer_id text NOT NULL,
              status text NOT NULL DEFAULT 'ACTIVE',
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS cart.cart_item (
              cart_id text NOT NULL REFERENCES cart.cart(cart_id) ON DELETE CASCADE,
              product_id text NOT NULL,
              qty integer NOT NULL CHECK (qty > 0),
              unit_price_snapshot numeric(12,2) NOT NULL,
              currency text NOT NULL DEFAULT 'USD',
              PRIMARY KEY (cart_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS cart.outbox_event (
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
        INSERT INTO cart.outbox_event (id, aggregate_type, aggregate_id, event_type, payload, created_at)
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
                    "producer": "cart-service",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "key": {"cart_id": aggregate_id},
                    "data": payload,
                }
            ),
        },
    )


def fetch_cart(cur: psycopg.Cursor, cart_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT cart_id, customer_id, status
        FROM cart.cart
        WHERE cart_id = %(cart_id)s
        """,
        {"cart_id": cart_id},
    )
    cart = cur.fetchone()
    if not cart:
        raise HTTPException(status_code=404, detail="cart not found")

    cur.execute(
        """
        SELECT product_id, qty, unit_price_snapshot, currency
        FROM cart.cart_item
        WHERE cart_id = %(cart_id)s
        ORDER BY product_id
        """,
        {"cart_id": cart_id},
    )
    items = cur.fetchall()
    for it in items:
        it["unit_price_snapshot"] = float(it["unit_price_snapshot"])
    cart["items"] = items
    return cart


@app.on_event("startup")
def on_startup() -> None:
    bootstrap()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "cart"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "cart"}


@app.post("/api/v1/carts")
def create_cart(payload: CreateCartRequest) -> dict[str, str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cart_id, status
            FROM cart.cart
            WHERE customer_id = %(customer_id)s AND status = 'ACTIVE'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"customer_id": payload.customer_id},
        )
        existing = cur.fetchone()
        if existing:
            return {"cart_id": existing["cart_id"], "status": existing["status"]}

        cart_id = f"cart-{uuid.uuid4().hex[:8]}"
        cur.execute(
            """
            INSERT INTO cart.cart (cart_id, customer_id, status)
            VALUES (%(cart_id)s, %(customer_id)s, 'ACTIVE')
            """,
            {"cart_id": cart_id, "customer_id": payload.customer_id},
        )
        emit_outbox(
            cur,
            aggregate_type="cart.CartCreated.v1",
            aggregate_id=cart_id,
            event_type="CartCreated",
            payload={"cart_id": cart_id, "customer_id": payload.customer_id, "status": "ACTIVE"},
        )

    return {"cart_id": cart_id, "status": "ACTIVE"}


@app.get("/api/v1/carts/{cart_id}")
def get_cart(cart_id: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        return fetch_cart(cur, cart_id)


@app.post("/api/v1/carts/{cart_id}/items")
def add_cart_item(cart_id: str, payload: AddItemRequest) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM cart.cart WHERE cart_id = %(cart_id)s", {"cart_id": cart_id})
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="cart not found")

        cur.execute(
            """
            SELECT product_id, price, currency
            FROM catalog.product
            WHERE product_id = %(product_id)s AND is_active = true
            """,
            {"product_id": payload.product_id},
        )
        product = cur.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="product not found")

        cur.execute(
            """
            INSERT INTO cart.cart_item (cart_id, product_id, qty, unit_price_snapshot, currency)
            VALUES (%(cart_id)s, %(product_id)s, %(qty)s, %(price)s, %(currency)s)
            ON CONFLICT (cart_id, product_id)
            DO UPDATE SET qty = cart.cart_item.qty + EXCLUDED.qty
            """,
            {
                "cart_id": cart_id,
                "product_id": payload.product_id,
                "qty": payload.qty,
                "price": product["price"],
                "currency": product["currency"],
            },
        )
        cur.execute(
            "UPDATE cart.cart SET updated_at = now() WHERE cart_id = %(cart_id)s",
            {"cart_id": cart_id},
        )

        emit_outbox(
            cur,
            aggregate_type="cart.CartItemAdded.v1",
            aggregate_id=cart_id,
            event_type="CartItemAdded",
            payload={"cart_id": cart_id, "product_id": payload.product_id, "qty": payload.qty},
        )

        return fetch_cart(cur, cart_id)


@app.patch("/api/v1/carts/{cart_id}/items/{product_id}")
def update_cart_item(cart_id: str, product_id: str, payload: UpdateItemRequest) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cart.cart_item
            SET qty = %(qty)s
            WHERE cart_id = %(cart_id)s AND product_id = %(product_id)s
            """,
            {"qty": payload.qty, "cart_id": cart_id, "product_id": product_id},
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="cart item not found")

        cur.execute(
            "UPDATE cart.cart SET updated_at = now() WHERE cart_id = %(cart_id)s",
            {"cart_id": cart_id},
        )

        emit_outbox(
            cur,
            aggregate_type="cart.CartItemUpdated.v1",
            aggregate_id=cart_id,
            event_type="CartItemUpdated",
            payload={"cart_id": cart_id, "product_id": product_id, "qty": payload.qty},
        )

        return fetch_cart(cur, cart_id)


@app.delete("/api/v1/carts/{cart_id}/items/{product_id}")
def delete_cart_item(cart_id: str, product_id: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM cart.cart_item WHERE cart_id = %(cart_id)s AND product_id = %(product_id)s",
            {"cart_id": cart_id, "product_id": product_id},
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="cart item not found")

        cur.execute(
            "UPDATE cart.cart SET updated_at = now() WHERE cart_id = %(cart_id)s",
            {"cart_id": cart_id},
        )

        emit_outbox(
            cur,
            aggregate_type="cart.CartItemRemoved.v1",
            aggregate_id=cart_id,
            event_type="CartItemRemoved",
            payload={"cart_id": cart_id, "product_id": product_id},
        )

        return fetch_cart(cur, cart_id)
