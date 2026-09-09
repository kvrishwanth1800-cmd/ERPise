"""PostgreSQL persistence for tenant-scoped fulfillment state and outbox events."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol


class DatabaseCursor(Protocol):
    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> None: ...

    def fetchone(self) -> tuple[Any, ...] | None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


class DatabaseConnection(Protocol):
    def cursor(self) -> DatabaseCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class StoredFulfillment:
    fulfillment_id: str
    tenant_id: str
    store_id: str
    warehouse_id: str
    order_id: str
    customer_id: str
    payment_id: str
    reservation_id: str
    method: str
    slot_id: str
    address: str | None
    status: str
    driver_id: str | None
    pickup_confirmation: str | None
    delivery_proof: str | None
    follow_up: str | None


class PostgresFulfillmentStore:
    """Uses one database transaction for fulfillment, transition, and outbox changes."""

    def __init__(self, connection: DatabaseConnection) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[DatabaseCursor]:
        cursor = self._connection.cursor()
        try:
            yield cursor
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def reserve_slot(
        self,
        cursor: DatabaseCursor,
        tenant_id: str,
        store_id: str,
        warehouse_id: str,
        slot_id: str,
    ) -> None:
        cursor.execute(
            "UPDATE fulfillment_slots SET reserved = reserved + 1 "
            "WHERE tenant_id = %s AND store_id = %s AND warehouse_id = %s "
            "AND slot_id = %s AND reserved < capacity",
            (tenant_id, store_id, warehouse_id, slot_id),
        )

    def write_transition_and_outbox(
        self,
        cursor: DatabaseCursor,
        fulfillment_id: str,
        tenant_id: str,
        from_status: str | None,
        to_status: str,
        actor_id: str,
        trace_id: str,
        transition_id: str,
        event_id: str,
    ) -> None:
        cursor.execute(
            "INSERT INTO fulfillment_transitions "
            "(transition_id, fulfillment_id, tenant_id, from_status, to_status, "
            "actor_id, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                transition_id,
                fulfillment_id,
                tenant_id,
                from_status,
                to_status,
                actor_id,
                trace_id,
            ),
        )
        payload = json.dumps({"fulfillment_id": fulfillment_id, "status": to_status})
        cursor.execute(
            "INSERT INTO fulfillment_outbox "
            "(event_id, fulfillment_id, tenant_id, event_type, trace_id, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (
                event_id,
                fulfillment_id,
                tenant_id,
                "fulfillment.changed.v1",
                trace_id,
                payload,
            ),
        )

    def active_for_tenant(self, cursor: DatabaseCursor, tenant_id: str) -> list[StoredFulfillment]:
        cursor.execute(
            "SELECT fulfillment_id, tenant_id, store_id, warehouse_id, order_id, customer_id, "
            "payment_id, reservation_id, method, slot_id, address, status, driver_id, "
            "pickup_confirmation, delivery_proof, follow_up FROM fulfillment_promises "
            "WHERE tenant_id = %s AND status NOT IN ('completed', 'cancelled', 'collected')",
            (tenant_id,),
        )
        return [StoredFulfillment(*row) for row in cursor.fetchall()]
