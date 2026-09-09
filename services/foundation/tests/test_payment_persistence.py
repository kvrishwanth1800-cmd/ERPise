from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from foundation.payment import PaymentIntent, PaymentTransaction, SettlementException
from foundation.payment_persistence import DurablePaymentStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
UP = tuple(
    f"{number:04d}_{name}.up.sql"
    for number, name in (
        (1, "operations_evidence"),
        (2, "foundation_durable_state"),
        (3, "durable_outbox_replay"),
        (4, "product_information"),
        (5, "catalog_assortment"),
        (6, "pricing_promotions"),
        (7, "inventory_ledger"),
        (8, "customer_consent_loyalty"),
        (9, "payment_orchestration"),
    )
)


@pytest.fixture()
def database() -> Iterator[psycopg.Connection[object]]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = psycopg.connect(DATABASE_URL)
    for name in UP:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection
    for name in reversed(UP):
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name.replace(".up.sql", ".down.sql")).read_text())
        connection.commit()
    connection.close()


def test_payment_facts_and_events_are_durable_and_tenant_scoped(
    database: psycopg.Connection[object],
) -> None:
    store = DurablePaymentStore(database)
    store.create_intent(
        PaymentIntent("intent-1", "tenant-a", "order-1", Decimal("20.00"), "USD", NOW),
        "intent-trace",
    )
    store.capture(
        PaymentTransaction(
            "txn-1", "tenant-a", "intent-1", "idem-1", Decimal("20.00"), "prov-ref", NOW
        ),
        "capture-trace",
    )
    store.report_settlement_exception(
        SettlementException(
            "exc-1", "tenant-a", "txn-1", Decimal("20.00"), Decimal("19.00"), "fee mismatch", NOW
        ),
        "settlement-trace",
    )

    with database.cursor() as cursor:
        cursor.execute(
            "SELECT event_type FROM durable_outbox_records WHERE tenant_id = 'tenant-a' "
            "ORDER BY event_type"
        )
        assert cursor.fetchall() == [
            ("PaymentChanged",),
            ("PaymentChanged",),
            ("SettlementReconciliationReported",),
        ]
        cursor.execute("SELECT count(*) FROM payment_intents WHERE tenant_id = 'tenant-b'")
        assert cursor.fetchone() == (0,)


def test_payment_transactions_are_append_only(database: psycopg.Connection[object]) -> None:
    store = DurablePaymentStore(database)
    store.create_intent(
        PaymentIntent("intent-1", "tenant-a", "order-1", Decimal("20.00"), "USD", NOW),
        "intent-trace",
    )
    store.capture(
        PaymentTransaction(
            "txn-1", "tenant-a", "intent-1", "idem-1", Decimal("20.00"), "prov-ref", NOW
        ),
        "capture-trace",
    )

    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute(
                "UPDATE payment_transactions SET amount = 1 WHERE transaction_id = 'txn-1'"
            )
        database.rollback()
