# ruff: noqa: E501
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
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
from foundation.procurement_persistence import DurableProcurementStore

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
        (12, "supplier_contract_governance"),
        (13, "procurement_lifecycle"),
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


def requisition(requisition_id: str = "req-1", tenant_id: str = "tenant-a") -> Requisition:
    return Requisition(
        requisition_id, tenant_id, "buyer", f"idem-{requisition_id}", "office supplies", Decimal("500.00"), "USD", NOW
    )


def quote(quote_id: str, tenant_id: str = "tenant-a") -> Quote:
    return Quote(quote_id, tenant_id, "req-1", "supplier-1", Decimal("480.00"), "USD", NOW)


def award(tenant_id: str = "tenant-a") -> Award:
    return Award(
        "award-1", tenant_id, "req-1", "supplier-1", Decimal("480.00"), "USD", ("quote-1", "quote-2"), "best value", NOW
    )


def po_line() -> PurchaseOrderLine:
    return PurchaseOrderLine("line-1", "sku-1", Decimal("10"), Decimal("48.00"), Decimal("480.00"))


def purchase_order(po_id: str = "po-1", tenant_id: str = "tenant-a") -> PurchaseOrder:
    return PurchaseOrder(
        po_id, tenant_id, "award-1", "supplier-1", "contract-1", f"po-idem-{po_id}",
        (po_line(),), Decimal("480.00"), "USD", NOW,
    )


def history(
    po_id: str, sequence: int, transition: str, from_status: str, to_status: str, tenant_id: str = "tenant-a"
) -> PurchaseOrderHistoryEntry:
    return PurchaseOrderHistoryEntry(
        f"{po_id}-{sequence}", tenant_id, po_id, sequence, transition, from_status, to_status, transition, NOW
    )


def test_procurement_lifecycle_facts_and_history_are_durable(
    database: psycopg.Connection[object],
) -> None:
    store = DurableProcurementStore(database)
    store.submit_requisition(requisition(), "req-trace")
    store.approve_requisition("tenant-a", "req-1", "approve-trace")
    store.record_quote(quote("quote-1"), "quote-trace-1")
    store.record_quote(quote("quote-2"), "quote-trace-2")
    store.record_award(award(), "award-trace")
    store.issue_purchase_order(purchase_order(), history("po-1", 1, "issued", "new", "issued"), "issue-trace")
    store.acknowledge(
        Acknowledgment("ack-1", "tenant-a", "po-1", "supplier-ref-1", NOW),
        history("po-1", 2, "acknowledged", "issued", "acknowledged"),
        "ack-trace",
    )
    store.receive(
        AdvanceShipmentNotice("asn-1", "tenant-a", "po-1", "line-1", Decimal("10"), "full shipment", NOW),
        history("po-1", 3, "received", "acknowledged", "received"),
        "received",
        "receive-trace",
    )
    store.close(
        PurchaseOrderClosure("closure-1", "tenant-a", "po-1", "complete", NOW),
        history("po-1", 4, "closed", "received", "closed"),
        "close-trace",
    )

    assert store.history("tenant-a", "po-1") == ("issued", "acknowledged", "received", "closed")
    persisted = store.purchase_order("tenant-a", "po-1")
    assert persisted is not None
    assert persisted.status == "closed"
    assert persisted.lines == (po_line(),)
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM durable_outbox_records WHERE tenant_id = 'tenant-a' "
            "AND event_type IN ('PurchaseOrderChanged', 'AsnReceived', 'RequisitionChanged', 'QuoteRecorded')"
        )
        assert cursor.fetchone()[0] >= 7


def test_purchase_orders_are_scoped_to_their_tenant(database: psycopg.Connection[object]) -> None:
    store = DurableProcurementStore(database)
    store.submit_requisition(requisition(), "req-trace")
    store.approve_requisition("tenant-a", "req-1", "approve-trace")
    store.record_quote(quote("quote-1"), "quote-trace-1")
    store.record_quote(quote("quote-2"), "quote-trace-2")
    store.record_award(award(), "award-trace")
    store.issue_purchase_order(purchase_order(), history("po-1", 1, "issued", "new", "issued"), "issue-trace")

    assert store.purchase_order("tenant-b", "po-1") is None


def test_duplicate_purchase_order_idempotency_keys_are_rejected_by_the_database(
    database: psycopg.Connection[object],
) -> None:
    store = DurableProcurementStore(database)
    store.submit_requisition(requisition(), "req-trace")
    store.approve_requisition("tenant-a", "req-1", "approve-trace")
    store.record_quote(quote("quote-1"), "quote-trace-1")
    store.record_quote(quote("quote-2"), "quote-trace-2")
    store.record_award(award(), "award-trace")
    store.issue_purchase_order(purchase_order(), history("po-1", 1, "issued", "new", "issued"), "issue-trace")

    duplicate = purchase_order(po_id="po-9")
    store.issue_purchase_order(duplicate, history("po-9", 1, "issued", "new", "issued"), "issue-trace-dup")

    assert store.purchase_order("tenant-a", "po-9") is None


def test_award_evidence_is_append_only(database: psycopg.Connection[object]) -> None:
    store = DurableProcurementStore(database)
    store.submit_requisition(requisition(), "req-trace")
    store.approve_requisition("tenant-a", "req-1", "approve-trace")
    store.record_quote(quote("quote-1"), "quote-trace-1")
    store.record_quote(quote("quote-2"), "quote-trace-2")
    store.record_award(award(), "award-trace")

    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("UPDATE awards SET rationale = 'changed' WHERE award_id = 'award-1'")
        database.rollback()
    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("DELETE FROM awards WHERE award_id = 'award-1'")
        database.rollback()
