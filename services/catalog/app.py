import os
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from psycopg.rows import dict_row


app = FastAPI(title="catalog-service", version="0.1.0")


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
            CREATE SCHEMA IF NOT EXISTS catalog;
            CREATE TABLE IF NOT EXISTS catalog.product (
              product_id text PRIMARY KEY,
              sku text UNIQUE,
              name text NOT NULL,
              description text,
              price numeric(12,2) NOT NULL,
              currency text NOT NULL DEFAULT 'USD',
              stock_qty integer NOT NULL DEFAULT 100,
              is_active boolean NOT NULL DEFAULT true
            );
            """
        )
        cur.execute(
            """
            INSERT INTO catalog.product (product_id, sku, name, description, price, currency, stock_qty, is_active)
            VALUES
              ('sku-1', 'SKU-1', 'Mechanical Keyboard', 'Hot-swap TKL keyboard', 50.00, 'USD', 100, true),
              ('sku-2', 'SKU-2', 'Gaming Mouse', 'Ergonomic RGB mouse', 35.00, 'USD', 80, true),
              ('sku-3', 'SKU-3', 'USB-C Dock', '7-in-1 USB-C dock', 79.00, 'USD', 40, true)
            ON CONFLICT (product_id) DO NOTHING;
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    bootstrap()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "catalog"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "catalog"}


@app.get("/api/v1/products")
def list_products(
    search: str = "",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    offset = (page - 1) * size
    with get_conn() as conn, conn.cursor() as cur:
        if search:
            cur.execute(
                """
                SELECT product_id, sku, name, description, price, currency, stock_qty, is_active
                FROM catalog.product
                WHERE is_active = true
                  AND (name ILIKE %(q)s OR sku ILIKE %(q)s OR product_id ILIKE %(q)s)
                ORDER BY name
                LIMIT %(size)s OFFSET %(offset)s
                """,
                {"q": f"%{search}%", "size": size, "offset": offset},
            )
        else:
            cur.execute(
                """
                SELECT product_id, sku, name, description, price, currency, stock_qty, is_active
                FROM catalog.product
                WHERE is_active = true
                ORDER BY name
                LIMIT %(size)s OFFSET %(offset)s
                """,
                {"size": size, "offset": offset},
            )
        rows = cur.fetchall()

    for r in rows:
        r["price"] = float(r["price"])
    return rows


@app.get("/api/v1/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT product_id, sku, name, description, price, currency, stock_qty, is_active
            FROM catalog.product
            WHERE product_id = %(product_id)s
            """,
            {"product_id": product_id},
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="product not found")

    row["price"] = float(row["price"])
    return row
