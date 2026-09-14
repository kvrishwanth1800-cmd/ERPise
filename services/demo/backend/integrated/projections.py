# ruff: noqa: E501
"""Read adapter for replay-safe order projections."""

from __future__ import annotations

from integrated.persistence import connect


def orders(tenant_id: str) -> list[dict[str, str]]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT order_id, fulfillment_status, event_id FROM demo_event_projection WHERE tenant_id = %s ORDER BY updated_at", (tenant_id,))
        rows = cursor.fetchall()
    return [{"order_id": str(row[0]), "fulfillment_status": str(row[1]), "event_id": str(row[2])} for row in rows]
