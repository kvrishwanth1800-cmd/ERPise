"""Tenant-scoped sales reporting adapter."""

from __future__ import annotations

from integrated.persistence import connect


def sales(tenant_id: str) -> dict[str, int]:
    """Return durable order count and gross sales for one tenant."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*), COALESCE(SUM(product.price_cents * orders.quantity), 0)
            FROM demo_orders AS orders
            JOIN demo_products AS product
              ON product.tenant_id = orders.tenant_id AND product.product_id = orders.product_id
            WHERE orders.tenant_id = %s""",
            (tenant_id,),
        )
        row = cursor.fetchone()
    return {"orders": int(row[0]), "gross_sales_cents": int(row[1])}
