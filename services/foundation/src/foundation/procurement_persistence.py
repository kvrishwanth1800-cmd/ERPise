# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.procurement import (
    Acknowledgment,
    AdvanceShipmentNotice,
    Award,
    PurchaseOrder,
    PurchaseOrderCancellation,
    PurchaseOrderChange,
    PurchaseOrderClosure,
    PurchaseOrderHistoryEntry,
    PurchaseOrderLine,
    Quote,
    Requisition,
)


class DurableProcurementStore:
    """Persists procurement lifecycle facts durably, atomically with their outbox events."""

    def __init__(self, connection: psycopg.Connection[object]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def submit_requisition(self, requisition: Requisition, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO requisitions (
                    tenant_id, requisition_id, requester_id, idempotency_key,
                    description, estimated_amount, currency, status, submitted_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                """,
                (
                    requisition.tenant_id,
                    requisition.requisition_id,
                    requisition.requester_id,
                    requisition.idempotency_key,
                    requisition.description,
                    requisition.estimated_amount,
                    requisition.currency,
                    requisition.status,
                    requisition.submitted_at,
                ),
            )
            return cursor.rowcount > 0

        self._commit(requisition.tenant_id, "RequisitionChanged", requisition.requisition_id, trace_id, write)

    def approve_requisition(self, tenant_id: str, requisition_id: str, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                UPDATE requisitions SET status = 'approved'
                WHERE tenant_id = %s AND requisition_id = %s AND status <> 'approved'
                """,
                (tenant_id, requisition_id),
            )
            return cursor.rowcount > 0

        self._commit(tenant_id, "RequisitionChanged", requisition_id, trace_id, write)

    def record_quote(self, quote: Quote, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO quotes (
                    tenant_id, quote_id, requisition_id, supplier_id, amount, currency, submitted_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    quote.tenant_id,
                    quote.quote_id,
                    quote.requisition_id,
                    quote.supplier_id,
                    quote.amount,
                    quote.currency,
                    quote.submitted_at,
                ),
            )

        self._commit(quote.tenant_id, "QuoteRecorded", quote.quote_id, trace_id, write)

    def record_award(self, award: Award, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO awards (
                    tenant_id, award_id, requisition_id, supplier_id, awarded_amount,
                    currency, compared_quote_ids, rationale, awarded_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    award.tenant_id,
                    award.award_id,
                    award.requisition_id,
                    award.supplier_id,
                    award.awarded_amount,
                    award.currency,
                    json.dumps(list(award.compared_quote_ids)),
                    award.rationale,
                    award.awarded_at,
                ),
            )

        self._commit(award.tenant_id, "PurchaseOrderChanged", award.award_id, trace_id, write)

    def issue_purchase_order(
        self, po: PurchaseOrder, history: PurchaseOrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO purchase_orders (
                    tenant_id, po_id, award_id, supplier_id, contract_id, idempotency_key,
                    lines, total_amount, currency, status, issued_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                """,
                (
                    po.tenant_id,
                    po.po_id,
                    po.award_id,
                    po.supplier_id,
                    po.contract_id,
                    po.idempotency_key,
                    json.dumps(
                        [
                            {
                                "line_id": line.line_id,
                                "product_id": line.product_id,
                                "quantity": str(line.quantity),
                                "unit_price": str(line.unit_price),
                                "line_total": str(line.line_total),
                            }
                            for line in po.lines
                        ]
                    ),
                    po.total_amount,
                    po.currency,
                    po.status,
                    po.issued_at,
                ),
            )
            if cursor.rowcount == 0:
                return False
            self._insert_history(cursor, history)
            return True

        self._commit(po.tenant_id, "PurchaseOrderChanged", po.po_id, trace_id, write)

    def acknowledge(
        self, acknowledgment: Acknowledgment, history: PurchaseOrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO purchase_order_acknowledgments (
                    tenant_id, acknowledgment_id, po_id, supplier_reference, acknowledged_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    acknowledgment.tenant_id,
                    acknowledgment.acknowledgment_id,
                    acknowledgment.po_id,
                    acknowledgment.supplier_reference,
                    acknowledgment.acknowledged_at,
                ),
            )
            self._set_status(cursor, acknowledgment.tenant_id, acknowledgment.po_id, "acknowledged")
            self._insert_history(cursor, history)
            return True

        self._commit(
            acknowledgment.tenant_id, "PurchaseOrderChanged", acknowledgment.acknowledgment_id, trace_id, write
        )

    def change_purchase_order(
        self, change: PurchaseOrderChange, history: PurchaseOrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO purchase_order_changes (tenant_id, change_id, po_id, reason, changed_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (change.tenant_id, change.change_id, change.po_id, change.reason, change.changed_at),
            )
            self._insert_history(cursor, history)
            return True

        self._commit(change.tenant_id, "PurchaseOrderChanged", change.change_id, trace_id, write)

    def receive(
        self,
        asn: AdvanceShipmentNotice,
        history: PurchaseOrderHistoryEntry,
        new_status: str,
        trace_id: str,
    ) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO purchase_order_asns (
                    tenant_id, asn_id, po_id, line_id, quantity_received, reason, received_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    asn.tenant_id,
                    asn.asn_id,
                    asn.po_id,
                    asn.line_id,
                    asn.quantity_received,
                    asn.reason,
                    asn.received_at,
                ),
            )
            self._set_status(cursor, asn.tenant_id, asn.po_id, new_status)
            self._insert_history(cursor, history)
            return True

        self._commit(asn.tenant_id, "AsnReceived", asn.asn_id, trace_id, write)

    def close(self, closure: PurchaseOrderClosure, history: PurchaseOrderHistoryEntry, trace_id: str) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO purchase_order_closures (tenant_id, closure_id, po_id, reason, closed_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (closure.tenant_id, closure.closure_id, closure.po_id, closure.reason, closure.closed_at),
            )
            self._set_status(cursor, closure.tenant_id, closure.po_id, "closed")
            self._insert_history(cursor, history)
            return True

        self._commit(closure.tenant_id, "PurchaseOrderChanged", closure.closure_id, trace_id, write)

    def cancel(
        self, cancellation: PurchaseOrderCancellation, history: PurchaseOrderHistoryEntry, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[object]) -> bool | None:
            cursor.execute(
                """
                INSERT INTO purchase_order_cancellations (tenant_id, cancellation_id, po_id, reason, cancelled_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    cancellation.tenant_id,
                    cancellation.cancellation_id,
                    cancellation.po_id,
                    cancellation.reason,
                    cancellation.cancelled_at,
                ),
            )
            self._set_status(cursor, cancellation.tenant_id, cancellation.po_id, "cancelled")
            self._insert_history(cursor, history)
            return True

        self._commit(cancellation.tenant_id, "PurchaseOrderChanged", cancellation.cancellation_id, trace_id, write)

    def purchase_order(self, tenant_id: str, po_id: str) -> PurchaseOrder | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT award_id, supplier_id, contract_id, idempotency_key, lines,
                       total_amount, currency, status, issued_at
                FROM purchase_orders WHERE tenant_id = %s AND po_id = %s
                """,
                (tenant_id, po_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            (
                award_id,
                supplier_id,
                contract_id,
                idempotency_key,
                lines,
                total_amount,
                currency,
                status,
                issued_at,
            ) = row
            parsed_lines = tuple(
                PurchaseOrderLine(
                    entry["line_id"],
                    entry["product_id"],
                    Decimal(entry["quantity"]),
                    Decimal(entry["unit_price"]),
                    Decimal(entry["line_total"]),
                )
                for entry in lines
            )
            return PurchaseOrder(
                po_id,
                tenant_id,
                award_id,
                supplier_id,
                contract_id,
                idempotency_key,
                parsed_lines,
                total_amount,
                currency,
                issued_at,
                status,
            )

    def history(self, tenant_id: str, po_id: str) -> tuple[str, ...]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT transition FROM purchase_order_history
                WHERE tenant_id = %s AND po_id = %s ORDER BY sequence ASC
                """,
                (tenant_id, po_id),
            )
            return tuple(row[0] for row in cursor.fetchall())

    def _insert_history(self, cursor: psycopg.Cursor[object], history: PurchaseOrderHistoryEntry) -> None:
        cursor.execute(
            """
            INSERT INTO purchase_order_history (
                tenant_id, entry_id, po_id, sequence, transition, from_status, to_status, detail, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                history.tenant_id,
                history.entry_id,
                history.po_id,
                history.sequence,
                history.transition,
                history.from_status,
                history.to_status,
                history.detail,
                history.occurred_at,
            ),
        )

    @staticmethod
    def _set_status(cursor: psycopg.Cursor[object], tenant_id: str, po_id: str, status: str) -> None:
        cursor.execute(
            "UPDATE purchase_orders SET status = %s WHERE tenant_id = %s AND po_id = %s",
            (status, tenant_id, po_id),
        )

    def _commit(
        self,
        tenant_id: str,
        event_type: str,
        subject_id: str,
        trace_id: str,
        write: Callable[[psycopg.Cursor[object]], bool | None],
    ) -> None:
        event = DurableEvent(
            f"{event_type}-{tenant_id}-{subject_id}",
            tenant_id,
            event_type,
            "v1",
            trace_id,
            {"subject_id": subject_id},
            datetime.now(tz=None).astimezone(),
        )
        self._outbox.commit_business_event(event, write)
