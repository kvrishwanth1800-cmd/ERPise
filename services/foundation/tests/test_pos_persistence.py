from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from foundation.pos import RegisterShift, Return, ReturnAuthorization, Sale, SaleLine
from foundation.pos_persistence import DurablePosStore

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
        (10, "counter_pos_register"),
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


def test_pos_facts_and_events_are_durable_and_tenant_scoped(
    database: psycopg.Connection[object],
) -> None:
    store = DurablePosStore(database)
    store.open_shift(
        RegisterShift(
            "shift-1", "tenant-a", "store-1", "register-1", "clerk", Decimal("100.00"), NOW
        ),
        "shift-trace",
    )
    line = SaleLine("sku-1", Decimal("2"), Decimal("5.00"), Decimal("10.00"))
    store.record_sale(
        Sale(
            "sale-1",
            "tenant-a",
            "shift-1",
            "idem-1",
            (line,),
            Decimal("10.00"),
            "USD",
            "cash",
            "txn-1",
            ("move-1",),
            "receipt-1",
            "online",
            NOW,
        ),
        "sale-trace",
    )
    store.authorize_return(
        ReturnAuthorization("auth-1", "tenant-a", "sale-1", "supervisor", NOW), "authorize-trace"
    )
    store.record_return(
        Return(
            "return-1",
            "tenant-a",
            "sale-1",
            "idem-return-1",
            (line,),
            Decimal("10.00"),
            "cash",
            "refund-1",
            ("move-return-1",),
            "customer request",
            NOW,
        ),
        "return-trace",
    )
    store.close_shift(
        RegisterShift(
            "shift-1",
            "tenant-a",
            "store-1",
            "register-1",
            "clerk",
            Decimal("100.00"),
            NOW,
            status="closed",
            declared_cash=Decimal("100.00"),
            recorded_cash=Decimal("100.00"),
            variance=Decimal("0.00"),
            closed_by="clerk",
            closed_at=NOW,
        ),
        "close-trace",
    )

    with database.cursor() as cursor:
        cursor.execute(
            "SELECT event_type FROM durable_outbox_records WHERE tenant_id = 'tenant-a' "
            "ORDER BY event_type"
        )
        assert cursor.fetchall() == [
            ("ReturnAuthorized",),
            ("ReturnCompleted",),
            ("SaleCompleted",),
            ("ShiftClosed",),
            ("ShiftOpened",),
        ]
        cursor.execute(
            "SELECT status FROM register_shifts WHERE tenant_id = 'tenant-a' AND shift_id = "
            "'shift-1'"
        )
        assert cursor.fetchone() == ("closed",)
        cursor.execute("SELECT count(*) FROM pos_sales WHERE tenant_id = 'tenant-b'")
        assert cursor.fetchone() == (0,)


def test_pos_sales_are_append_only(database: psycopg.Connection[object]) -> None:
    store = DurablePosStore(database)
    store.open_shift(
        RegisterShift(
            "shift-1", "tenant-a", "store-1", "register-1", "clerk", Decimal("100.00"), NOW
        ),
        "shift-trace",
    )
    line = SaleLine("sku-1", Decimal("2"), Decimal("5.00"), Decimal("10.00"))
    store.record_sale(
        Sale(
            "sale-1",
            "tenant-a",
            "shift-1",
            "idem-1",
            (line,),
            Decimal("10.00"),
            "USD",
            "cash",
            "txn-1",
            ("move-1",),
            "receipt-1",
            "online",
            NOW,
        ),
        "sale-trace",
    )

    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("UPDATE pos_sales SET total_amount = 1 WHERE sale_id = 'sale-1'")
        database.rollback()
