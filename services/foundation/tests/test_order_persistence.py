# ruff: noqa: E501
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from foundation.order import (
    Order,
    OrderAllocation,
    OrderCancellation,
    OrderFulfillment,
    OrderHistoryEntry,
    OrderLine,
    OrderRefund,
    OrderReturn,
)
from foundation.order_persistence import DurableOrderStore

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
        (11, "order_management"),
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


def line() -> OrderLine:
    return OrderLine("line-1", "sku-1", Decimal("2"), Decimal("5.00"), Decimal("10.00"))


def order(order_id: str = "order-1", tenant_id: str = "tenant-a") -> Order:
    return Order(
        order_id,
        tenant_id,
        "storefront",
        "customer-1",
        f"idem-{order_id}",
        (line(),),
        Decimal("10.00"),
        "USD",
        "reservation-1",
        "txn-1",
        NOW,
    )


def history(
    order_id: str,
    sequence: int,
    transition: str,
    from_status: str,
    to_status: str,
    tenant_id: str = "tenant-a",
) -> OrderHistoryEntry:
    return OrderHistoryEntry(
        f"{order_id}-{sequence}",
        tenant_id,
        order_id,
        sequence,
        transition,
        from_status,
        to_status,
        transition,
        NOW,
    )


def test_order_lifecycle_facts_events_and_history_are_durable(
    database: psycopg.Connection[object],
) -> None:
    store = DurableOrderStore(database)
    store.place_order(order(), history("order-1", 1, "placed", "new", "placed"), "place-trace")
    store.allocate(
        OrderAllocation(
            "alloc-1", "tenant-a", "order-1", "line-1", "store-1", Decimal("2"), "move-1", NOW
        ),
        history("order-1", 2, "allocated", "placed", "allocated"),
        "allocate-trace",
    )
    store.fulfill(
        OrderFulfillment("fulfil-1", "tenant-a", "order-1", "carrier-1", NOW),
        history("order-1", 3, "fulfilled", "allocated", "fulfilled"),
        "fulfill-trace",
    )
    store.record_return(
        OrderReturn(
            "return-1",
            "tenant-a",
            "order-1",
            "idem-return-1",
            (line(),),
            Decimal("10.00"),
            "damaged",
            NOW,
        ),
        history("order-1", 4, "returned", "fulfilled", "returned"),
        "return-trace",
    )
    store.refund(
        OrderRefund(
            "refund-1",
            "tenant-a",
            "order-1",
            "return-1",
            "idem-refund-1",
            Decimal("10.00"),
            "payment-refund-1",
            NOW,
        ),
        history("order-1", 5, "refunded", "returned", "refunded"),
        "refund-trace",
    )

    assert store.history("tenant-a", "order-1") == (
        "placed",
        "allocated",
        "fulfilled",
        "returned",
        "refunded",
    )
    persisted = store.order("tenant-a", "order-1")
    assert persisted is not None
    assert persisted.status == "refunded"
    assert persisted.total_amount == Decimal("10.00")
    assert persisted.lines == (line(),)
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM durable_outbox_records WHERE tenant_id = 'tenant-a' AND event_type IN ('OrderChanged', 'FulfillmentChanged')"
        )
        assert cursor.fetchone() == (5,)


def test_orders_are_scoped_to_their_tenant(database: psycopg.Connection[object]) -> None:
    store = DurableOrderStore(database)
    store.place_order(order(), history("order-1", 1, "placed", "new", "placed"), "place-trace")
    store.place_order(
        order("order-2", "tenant-b"),
        history("order-2", 1, "placed", "new", "placed", "tenant-b"),
        "place-trace-b",
    )

    assert store.order("tenant-b", "order-1") is None
    assert store.order("tenant-a", "order-2") is None
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM orders WHERE tenant_id = 'tenant-b'")
        assert cursor.fetchone() == (1,)


def test_duplicate_checkout_keys_are_rejected_by_the_database(
    database: psycopg.Connection[object],
) -> None:
    store = DurableOrderStore(database)
    store.place_order(order(), history("order-1", 1, "placed", "new", "placed"), "place-trace")
    duplicate = Order(
        "order-9",
        "tenant-a",
        "storefront",
        "customer-1",
        "idem-order-1",
        (line(),),
        Decimal("10.00"),
        "USD",
        "reservation-1",
        "txn-1",
        NOW,
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        store.place_order(
            duplicate, history("order-9", 1, "placed", "new", "placed"), "place-trace-dup"
        )
    database.rollback()

    assert store.order("tenant-a", "order-9") is None


def test_order_lines_and_history_are_append_only(
    database: psycopg.Connection[object],
) -> None:
    store = DurableOrderStore(database)
    store.place_order(order(), history("order-1", 1, "placed", "new", "placed"), "place-trace")

    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("UPDATE orders SET total_amount = 1 WHERE order_id = 'order-1'")
        database.rollback()
    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("DELETE FROM order_history WHERE order_id = 'order-1'")
        database.rollback()


def test_cancellation_is_recorded_without_altering_committed_lines(
    database: psycopg.Connection[object],
) -> None:
    store = DurableOrderStore(database)
    store.place_order(order(), history("order-1", 1, "placed", "new", "placed"), "place-trace")
    store.cancel(
        OrderCancellation("cancel-1", "tenant-a", "order-1", "customer request", NOW),
        history("order-1", 2, "cancelled", "placed", "cancelled"),
        "cancel-trace",
    )

    persisted = store.order("tenant-a", "order-1")
    assert persisted is not None
    assert persisted.status == "cancelled"
    assert persisted.lines == (line(),)
    assert store.history("tenant-a", "order-1") == ("placed", "cancelled")
