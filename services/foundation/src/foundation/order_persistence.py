# ruff: noqa: E501
"""PostgreSQL persistence for order facts, lifecycle history, and integration events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.order import (
    Order,
    OrderAllocation,
    OrderCancellation,
    OrderFulfillment,
    OrderHistoryEntry,
    OrderLine,
    OrderRefund,
    OrderReturn,
    OrderSubstitution,
)


class DurableOrderStore:
    """Commits one order fact, its history entry, and its event per transaction."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def place_order(self, order: Order, history: OrderHistoryEntry, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO orders (
                    tenant_id, order_id, channel, customer_id, idempotency_key, lines,
                    total_amount, currency, reservation_id, payment_transaction_id, status,
                    placed_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                """,
                (
                    order.tenant_id,
                    order.order_id,
                    order.channel,
                    order.customer_id,
                    order.idempotency_key,
                    json.dumps([line.__dict__ for line in order.lines], default=str),
                    order.total_amount,
                    order.currency,
                    order.reservation_id,
                    order.payment_transaction_id,
                    order.status,
                    order.placed_at,
                ),
            )
            self._insert_history(cursor, history)

        self._commit(order.tenant_id, "OrderChanged", order.order_id, trace_id, write)

    def allocate(
        self, allocation: OrderAllocation, history: OrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO order_allocations (
                    tenant_id, allocation_id, order_id, line_id, location_id, quantity,
                    inventory_movement_id, allocated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    allocation.tenant_id,
                    allocation.allocation_id,
                    allocation.order_id,
                    allocation.line_id,
                    allocation.location_id,
                    allocation.quantity,
                    allocation.inventory_movement_id,
                    allocation.allocated_at,
                ),
            )
            self._set_status(cursor, allocation.tenant_id, allocation.order_id, "allocated")
            self._insert_history(cursor, history)

        self._commit(
            allocation.tenant_id,
            "FulfillmentChanged",
            allocation.allocation_id,
            trace_id,
            write,
        )

    def substitute(
        self, substitution: OrderSubstitution, history: OrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO order_substitutions (
                    tenant_id, substitution_id, order_id, line_id, substitute_product_id,
                    quantity, reason, substituted_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    substitution.tenant_id,
                    substitution.substitution_id,
                    substitution.order_id,
                    substitution.line_id,
                    substitution.substitute_product_id,
                    substitution.quantity,
                    substitution.reason,
                    substitution.substituted_at,
                ),
            )
            self._insert_history(cursor, history)

        self._commit(
            substitution.tenant_id,
            "FulfillmentChanged",
            substitution.substitution_id,
            trace_id,
            write,
        )

    def cancel(
        self, cancellation: OrderCancellation, history: OrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO order_cancellations (
                    tenant_id, cancellation_id, order_id, reason, cancelled_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    cancellation.tenant_id,
                    cancellation.cancellation_id,
                    cancellation.order_id,
                    cancellation.reason,
                    cancellation.cancelled_at,
                ),
            )
            self._set_status(cursor, cancellation.tenant_id, cancellation.order_id, "cancelled")
            self._insert_history(cursor, history)

        self._commit(
            cancellation.tenant_id,
            "OrderChanged",
            cancellation.cancellation_id,
            trace_id,
            write,
        )

    def fulfill(
        self, fulfillment: OrderFulfillment, history: OrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO order_fulfillments (
                    tenant_id, fulfillment_id, order_id, carrier_reference, fulfilled_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    fulfillment.tenant_id,
                    fulfillment.fulfillment_id,
                    fulfillment.order_id,
                    fulfillment.carrier_reference,
                    fulfillment.fulfilled_at,
                ),
            )
            self._set_status(cursor, fulfillment.tenant_id, fulfillment.order_id, "fulfilled")
            self._insert_history(cursor, history)

        self._commit(
            fulfillment.tenant_id,
            "FulfillmentChanged",
            fulfillment.fulfillment_id,
            trace_id,
            write,
        )

    def record_return(
        self, return_: OrderReturn, history: OrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO order_returns (
                    tenant_id, return_id, order_id, idempotency_key, lines, total_amount,
                    reason, returned_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    return_.tenant_id,
                    return_.return_id,
                    return_.order_id,
                    return_.idempotency_key,
                    json.dumps([line.__dict__ for line in return_.lines], default=str),
                    return_.total_amount,
                    return_.reason,
                    return_.returned_at,
                ),
            )
            self._set_status(cursor, return_.tenant_id, return_.order_id, "returned")
            self._insert_history(cursor, history)

        self._commit(return_.tenant_id, "OrderChanged", return_.return_id, trace_id, write)

    def refund(self, refund: OrderRefund, history: OrderHistoryEntry, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                """
                INSERT INTO order_refunds (
                    tenant_id, refund_id, order_id, return_id, idempotency_key, amount,
                    payment_refund_id, refunded_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    refund.tenant_id,
                    refund.refund_id,
                    refund.order_id,
                    refund.return_id,
                    refund.idempotency_key,
                    refund.amount,
                    refund.payment_refund_id,
                    refund.refunded_at,
                ),
            )
            self._set_status(cursor, refund.tenant_id, refund.order_id, "refunded")
            self._insert_history(cursor, history)

        self._commit(refund.tenant_id, "OrderChanged", refund.refund_id, trace_id, write)

    def order(self, tenant_id: str, order_id: str) -> Order | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT channel, customer_id, idempotency_key, lines, total_amount, currency, reservation_id, payment_transaction_id, status, placed_at FROM orders WHERE tenant_id = %s AND order_id = %s",
                (tenant_id, order_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            lines = tuple(
                OrderLine(
                    str(line["line_id"]),
                    str(line["product_id"]),
                    Decimal(str(line["quantity"])),
                    Decimal(str(line["unit_price"])),
                    Decimal(str(line["line_total"])),
                )
                for line in row[3]
            )
            return Order(
                order_id,
                tenant_id,
                str(row[0]),
                str(row[1]),
                str(row[2]),
                lines,
                Decimal(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
                row[9],
                str(row[8]),
            )

    def history(self, tenant_id: str, order_id: str) -> tuple[str, ...]:
        """Returns the order's recorded transitions in chronological sequence order."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT transition FROM order_history WHERE tenant_id = %s AND order_id = %s ORDER BY sequence",
                (tenant_id, order_id),
            )
            return tuple(str(transition) for (transition,) in cursor.fetchall())

    @staticmethod
    def _insert_history(cursor: psycopg.Cursor[Any], history: OrderHistoryEntry) -> None:
        cursor.execute(
            """
            INSERT INTO order_history (
                tenant_id, entry_id, order_id, sequence, transition, from_status, to_status,
                detail, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                history.tenant_id,
                history.entry_id,
                history.order_id,
                history.sequence,
                history.transition,
                history.from_status,
                history.to_status,
                history.detail,
                history.occurred_at,
            ),
        )

    @staticmethod
    def _set_status(
        cursor: psycopg.Cursor[Any], tenant_id: str, order_id: str, status: str
    ) -> None:
        cursor.execute(
            "UPDATE orders SET status = %s WHERE tenant_id = %s AND order_id = %s",
            (status, tenant_id, order_id),
        )

    def _commit(
        self,
        tenant_id: str,
        event_type: str,
        subject_id: str,
        trace_id: str,
        write: Any,
    ) -> None:
        event = DurableEvent(
            f"{event_type}-{tenant_id}-{subject_id}",
            tenant_id,
            event_type,
            "v1",
            trace_id,
            {"subject_id": subject_id},
            datetime.now(UTC),
        )
        self._outbox.commit_business_event(event, write)
