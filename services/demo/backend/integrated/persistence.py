# ruff: noqa: I001
"""PostgreSQL connection and idempotent Program A schema setup."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg


def connect() -> psycopg.Connection[tuple[object, ...]]:
    """Open a connection to the demonstration PostgreSQL database."""
    return psycopg.connect(os.environ["DATABASE_URL"])


def migrate() -> None:
    """Apply the small, idempotent Program A schema migrations."""
    migrations = Path("/app/migrations").glob("*.sql")
    with connect() as connection, connection.cursor() as cursor:
        for migration in sorted(migrations):
            cursor.execute(migration.read_text())
        cursor.execute(
            """INSERT INTO demo_products
            (tenant_id, product_id, sku, name, price_cents, available)
            VALUES ('demo-tenant', 'coffee', 'DEMO-COFFEE', 'Demo Coffee', 499, 100)
            ON CONFLICT (tenant_id, product_id) DO NOTHING"""
        )
        cursor.execute(
            """INSERT INTO demo_customers (tenant_id, customer_id, email, consented)
            VALUES ('demo-tenant', 'customer', 'customer@erpise.local', TRUE)
            ON CONFLICT (tenant_id, customer_id) DO NOTHING"""
        )
