"""PostgreSQL persistence for the payment orchestration local fake.

Payment records never carry cardholder data (PAN or CVV): only tokenized
provider references and computed amounts are persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.payment import (
    PaymentIntent,
    PaymentRefund,
    PaymentTransaction,
    SettlementException,
)


class DurablePaymentStore:
    """Commits payment facts and their integration events in one transaction."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._outbox = DurableOutboxStore(connection)

    def create_intent(self, intent: PaymentIntent, trace_id: str) -> None:
        self._commit(
            intent.tenant_id,
            "PaymentChanged",
            intent.intent_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO payment_intents (
                    tenant_id, intent_id, order_reference, amount, currency, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    intent.tenant_id,
                    intent.intent_id,
                    intent.order_reference,
                    intent.amount,
                    intent.currency,
                    intent.created_at,
                ),
            ),
        )

    def capture(self, transaction: PaymentTransaction, trace_id: str) -> None:
        self._commit(
            transaction.tenant_id,
            "PaymentChanged",
            transaction.transaction_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO payment_transactions (
                    tenant_id, transaction_id, intent_id, idempotency_key, amount,
                    provider_reference, captured_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    transaction.tenant_id,
                    transaction.transaction_id,
                    transaction.intent_id,
                    transaction.idempotency_key,
                    transaction.amount,
                    transaction.provider_reference,
                    transaction.captured_at,
                ),
            ),
        )

    def refund(self, refund: PaymentRefund, trace_id: str) -> None:
        self._commit(
            refund.tenant_id,
            "PaymentChanged",
            refund.refund_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO payment_refunds (
                    tenant_id, refund_id, transaction_id, idempotency_key, amount,
                    reason, refunded_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    refund.tenant_id,
                    refund.refund_id,
                    refund.transaction_id,
                    refund.idempotency_key,
                    refund.amount,
                    refund.reason,
                    refund.refunded_at,
                ),
            ),
        )

    def report_settlement_exception(
        self, exception: SettlementException, trace_id: str
    ) -> None:
        self._commit(
            exception.tenant_id,
            "SettlementReconciliationReported",
            exception.exception_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO payment_settlement_exceptions (
                    tenant_id, exception_id, transaction_id, expected_amount,
                    settled_amount, reason, reported_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    exception.tenant_id,
                    exception.exception_id,
                    exception.transaction_id,
                    exception.expected_amount,
                    exception.settled_amount,
                    exception.reason,
                    exception.reported_at,
                ),
            ),
        )

    def refunded_amount(self, tenant_id: str, transaction_id: str) -> object:
        connection = self._outbox._connection
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM payment_refunds
                WHERE tenant_id = %s AND transaction_id = %s
                """,
                (tenant_id, transaction_id),
            )
            row = cursor.fetchone()
            assert row is not None
            return row[0]

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
