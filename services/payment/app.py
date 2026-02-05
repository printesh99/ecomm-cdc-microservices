import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row


app = FastAPI(title="payment-service", version="0.1.0")


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


class CreatePaymentRequest(BaseModel):
    order_id: str
    amount: float = Field(gt=0)
    currency: str = "USD"
    method: str = "CARD"


class ConfirmPaymentRequest(BaseModel):
    result: str = "AUTHORIZED"


def bootstrap() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE SCHEMA IF NOT EXISTS payment;
            CREATE TABLE IF NOT EXISTS payment.payment (
              payment_id text PRIMARY KEY,
              order_id text NOT NULL,
              amount numeric(12,2) NOT NULL,
              currency text NOT NULL,
              method text NOT NULL,
              status text NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS payment.outbox_event (
              id uuid PRIMARY KEY,
              aggregate_type text NOT NULL,
              aggregate_id text NOT NULL,
              event_type text NOT NULL,
              payload jsonb NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now()
            );

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


def emit_outbox(cur: psycopg.Cursor, schema: str, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> None:
    event_id = uuid.uuid4()
    cur.execute(
        f"""
        INSERT INTO {schema}.outbox_event (id, aggregate_type, aggregate_id, event_type, payload, created_at)
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
                    "producer": f"{schema}-service",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "key": {"id": aggregate_id},
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
    return {"status": "ok", "service": "payment"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "payment"}


@app.post("/api/v1/payments")
def create_payment(payload: CreatePaymentRequest) -> dict[str, str]:
    payment_id = f"pay-{uuid.uuid4().hex[:8]}"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payment.payment (payment_id, order_id, amount, currency, method, status)
            VALUES (%(payment_id)s, %(order_id)s, %(amount)s, %(currency)s, %(method)s, 'INITIATED')
            """,
            {
                "payment_id": payment_id,
                "order_id": payload.order_id,
                "amount": payload.amount,
                "currency": payload.currency,
                "method": payload.method,
            },
        )

        emit_outbox(
            cur,
            schema="payment",
            aggregate_type="payment.PaymentInitiated.v1",
            aggregate_id=payload.order_id,
            event_type="PaymentInitiated",
            payload={
                "payment_id": payment_id,
                "order_id": payload.order_id,
                "amount": payload.amount,
                "currency": payload.currency,
                "method": payload.method,
                "status": "INITIATED",
            },
        )

    return {"payment_id": payment_id, "status": "INITIATED"}


@app.post("/api/v1/payments/{payment_id}/confirm")
def confirm_payment(payment_id: str, payload: ConfirmPaymentRequest) -> dict[str, str]:
    result = payload.result.upper()
    status = "AUTHORIZED" if result == "AUTHORIZED" else "FAILED"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE payment.payment
            SET status = %(status)s, updated_at = now()
            WHERE payment_id = %(payment_id)s
            RETURNING payment_id, order_id, amount, currency
            """,
            {"status": status, "payment_id": payment_id},
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="payment not found")

        event_type = "PaymentAuthorized" if status == "AUTHORIZED" else "PaymentFailed"
        aggregate_type = f"payment.{event_type}.v1"

        emit_outbox(
            cur,
            schema="payment",
            aggregate_type=aggregate_type,
            aggregate_id=row["order_id"],
            event_type=event_type,
            payload={
                "payment_id": payment_id,
                "order_id": row["order_id"],
                "amount": float(row["amount"]),
                "currency": row["currency"],
                "status": status,
            },
        )

        if status == "AUTHORIZED":
            cur.execute("SELECT to_regclass('orders.\"order\"') AS table_name")
            has_orders_table = cur.fetchone()
            if has_orders_table and has_orders_table["table_name"]:
                cur.execute(
                    """
                    UPDATE orders."order"
                    SET status = 'PAID', updated_at = now()
                    WHERE order_id = %(order_id)s
                    """,
                    {"order_id": row["order_id"]},
                )

            shipment_id = f"ship-{uuid.uuid4().hex[:8]}"
            cur.execute(
                """
                INSERT INTO shipping.shipment (shipment_id, order_id, status)
                VALUES (%(shipment_id)s, %(order_id)s, 'CREATED')
                ON CONFLICT (order_id) DO NOTHING
                """,
                {"shipment_id": shipment_id, "order_id": row["order_id"]},
            )
            cur.execute(
                """
                SELECT shipment_id
                FROM shipping.shipment
                WHERE order_id = %(order_id)s
                """,
                {"order_id": row["order_id"]},
            )
            shipment = cur.fetchone()

            emit_outbox(
                cur,
                schema="shipping",
                aggregate_type="shipping.ShipmentCreated.v1",
                aggregate_id=row["order_id"],
                event_type="ShipmentCreated",
                payload={
                    "shipment_id": shipment["shipment_id"] if shipment else shipment_id,
                    "order_id": row["order_id"],
                    "status": "CREATED",
                },
            )

    return {"payment_id": payment_id, "status": status}
