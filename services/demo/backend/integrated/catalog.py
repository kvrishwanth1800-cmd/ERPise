"""Tenant-scoped catalog and pricing read adapter."""

from __future__ import annotations

from typing import cast

from integrated.persistence import connect


def products(tenant_id: str) -> list[dict[str, object]]:
    """Read catalog, pricing, and available stock for one tenant."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT product_id, sku, name, price_cents, available FROM demo_products WHERE tenant_id = %s ORDER BY sku", (tenant_id,))
        rows = cursor.fetchall()
    return [{"id": str(row[0]), "sku": str(row[1]), "name": str(row[2]), "price_cents": cast(int, row[3]), "available": cast(int, row[4])} for row in rows]
