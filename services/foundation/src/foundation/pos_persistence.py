# ruff: noqa: E501
"""PostgreSQL persistence for counter POS shifts, sales, return authorizations, and returns."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.pos import RegisterShift, Return, ReturnAuthorization, Sale


class DurablePosStore:
    """Commits POS facts and their integration events in one transaction per operation."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._outbox = DurableOutboxStore(connection)

    def open_shift(self, shift: RegisterShift, trace_id: str) -> None:
        self._commit(
            shift.tenant_id,
            "ShiftOpened",
            shift.shift_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO register_shifts (
                    tenant_id, shift_id, store_id, register_id, opened_by, opening_float,
                    opened_at, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    shift.tenant_id,
                    shift.shift_id,
                    shift.store_id,
                    shift.register_id,
                    shift.opened_by,
                    shift.opening_float,
                    shift.opened_at,
                    shift.status,
                ),
            ),
        )

    def record_sale(self, sale: Sale, trace_id: str) -> None:
        self._commit(
            sale.tenant_id,
            "SaleCompleted",
            sale.sale_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO pos_sales (
                    tenant_id, sale_id, shift_id, idempotency_key, lines, total_amount,
                    currency, tender_type, payment_transaction_id, inventory_movement_ids,
                    receipt_id, connectivity_state, sold_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    sale.tenant_id,
                    sale.sale_id,
                    sale.shift_id,
                    sale.idempotency_key,
                    json.dumps([line.__dict__ for line in sale.lines], default=str),
                    sale.total_amount,
                    sale.currency,
                    sale.tender_type,
                    sale.payment_transaction_id,
                    json.dumps(list(sale.inventory_movement_ids)),
                    sale.receipt_id,
                    sale.connectivity_state,
                    sale.sold_at,
                ),
            ),
        )

    def authorize_return(self, authorization: ReturnAuthorization, trace_id: str) -> None:
        self._commit(
            authorization.tenant_id,
            "ReturnAuthorized",
            authorization.authorization_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO return_authorizations (
                    tenant_id, authorization_id, sale_id, authorized_by, granted_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    authorization.tenant_id,
                    authorization.authorization_id,
                    authorization.sale_id,
                    authorization.authorized_by,
                    authorization.granted_at,
                ),
            ),
        )

    def record_return(self, return_: Return, trace_id: str) -> None:
        self._commit(
            return_.tenant_id,
            "ReturnCompleted",
            return_.return_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO pos_returns (
                    tenant_id, return_id, original_sale_id, idempotency_key, lines,
                    total_amount, tender_type, refund_transaction_id, inventory_movement_ids,
                    reason, returned_at
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    return_.tenant_id,
                    return_.return_id,
                    return_.original_sale_id,
                    return_.idempotency_key,
                    json.dumps([line.__dict__ for line in return_.lines], default=str),
                    return_.total_amount,
                    return_.tender_type,
                    return_.refund_transaction_id,
                    json.dumps(list(return_.inventory_movement_ids)),
                    return_.reason,
                    return_.returned_at,
                ),
            ),
        )

    def close_shift(self, shift: RegisterShift, trace_id: str) -> None:
        self._commit(
            shift.tenant_id,
            "ShiftClosed",
            shift.shift_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                UPDATE register_shifts
                SET status = %s, declared_cash = %s, recorded_cash = %s, variance = %s,
                    closed_by = %s, closed_at = %s
                WHERE tenant_id = %s AND shift_id = %s
                """,
                (
                    shift.status,
                    shift.declared_cash,
                    shift.recorded_cash,
                    shift.variance,
                    shift.closed_by,
                    shift.closed_at,
                    shift.tenant_id,
                    shift.shift_id,
                ),
            ),
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
