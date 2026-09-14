# ruff: noqa: E501
"""Durable tenant-scoped order, payment, inventory, and outbox adapter."""

from __future__ import annotations

import json
import uuid
from typing import cast

from integrated.persistence import connect


def create(tenant_id: str, customer_id: str, product_id: str, quantity: int, method: str,
           idempotency_key: str) -> dict[str, object]:
    """Reserve stock and persist the order, payment state, and outbox event atomically."""
    if quantity < 1 or not idempotency_key or method not in {"pickup", "delivery"}:
        raise ValueError("invalid_checkout")
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT order_id, payment_id, fulfillment_status FROM demo_orders WHERE tenant_id = %s AND idempotency_key = %s", (tenant_id, idempotency_key))
        existing = cursor.fetchone()
        if existing is not None:
            return {"order_id": str(existing[0]), "payment_id": str(existing[1]), "fulfillment_status": str(existing[2]), "reservation": "reserved", "idempotent": True}
        cursor.execute("UPDATE demo_products SET available = available - %s WHERE tenant_id = %s AND product_id = %s AND available >= %s RETURNING price_cents", (quantity, tenant_id, product_id, quantity))
        product = cursor.fetchone()
        if product is None:
            raise ValueError("inventory_unavailable")
        order_id, payment_id, event_id = (f"order-{uuid.uuid4()}", f"payment-{uuid.uuid4()}", str(uuid.uuid4()))
        cursor.execute("INSERT INTO demo_orders (tenant_id, order_id, customer_id, product_id, quantity, payment_id, fulfillment_status, idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, 'reserved', %s)", (tenant_id, order_id, customer_id, product_id, quantity, payment_id, idempotency_key))
        payload = json.dumps({"order_id": order_id, "payment_id": payment_id, "fulfillment_status": "reserved", "product_id": product_id, "quantity": quantity, "price_cents": cast(int, product[0]), "method": method})
        cursor.execute("INSERT INTO demo_outbox (event_id, tenant_id, event_type, payload) VALUES (%s, %s, 'order.created.v1', %s::jsonb)", (event_id, tenant_id, payload))
    return {"order_id": order_id, "payment_id": payment_id, "fulfillment_status": "reserved", "reservation": "reserved", "idempotent": False}


def list_orders(tenant_id: str) -> list[dict[str, object]]:
    """Read orders and their durable projection state for one tenant."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT order_id, payment_id, fulfillment_status FROM demo_orders WHERE tenant_id = %s ORDER BY order_id", (tenant_id,))
        rows = cursor.fetchall()
    return [{"order_id": str(row[0]), "payment_id": str(row[1]), "fulfillment_status": str(row[2])} for row in rows]
