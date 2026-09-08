# ruff: noqa: E501
"""PostgreSQL persistence for immutable inventory movement facts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.inventory import InventoryMovement, InventoryPosition


class DurableInventoryLedger:
    """Posts immutable movements with an atomic durable event and derives positions."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def post(self, tenant_id: str, movement: InventoryMovement, trace_id: str) -> None:
        event = DurableEvent(f"InventoryMoved-{tenant_id}-{movement.movement_id}", tenant_id, "InventoryMoved", "v1", trace_id, {"movement_id": movement.movement_id, "product_id": movement.product_id, "location_id": movement.location_id, "quantity_type": movement.quantity_type, "quantity_delta": str(movement.quantity_delta), "reason": movement.reason}, datetime.now(UTC))
        self._outbox.commit_business_event(event, lambda cursor: self._insert(cursor, tenant_id, movement))

    def positions(self, tenant_id: str) -> tuple[InventoryPosition, ...]:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT product_id, location_id, quantity_type, SUM(quantity_delta) FROM inventory_movements WHERE tenant_id = %s GROUP BY product_id, location_id, quantity_type ORDER BY product_id, location_id, quantity_type", (tenant_id,))
            return tuple(InventoryPosition(str(product_id), str(location_id), str(quantity_type), Decimal(quantity)) for product_id, location_id, quantity_type, quantity in cursor.fetchall())

    def rebuild_positions(self, tenant_id: str, trace_id: str) -> tuple[InventoryPosition, ...]:
        positions = self.positions(tenant_id)
        event = DurableEvent(f"InventoryPositionRebuilt-{tenant_id}-{trace_id}", tenant_id, "InventoryPositionRebuilt", "v1", trace_id, {"position_count": len(positions)}, datetime.now(UTC))
        self._outbox.commit_business_event(event, lambda _cursor: None)
        return positions

    @staticmethod
    def _insert(cursor: psycopg.Cursor[Any], tenant_id: str, movement: InventoryMovement) -> None:
        cursor.execute("INSERT INTO inventory_movements (tenant_id, movement_id, product_id, location_id, quantity_type, quantity_delta, reason, occurred_at, reversal_of_movement_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (tenant_id, movement.movement_id, movement.product_id, movement.location_id, movement.quantity_type, movement.quantity_delta, movement.reason, movement.occurred_at, movement.reversal_of_movement_id))
