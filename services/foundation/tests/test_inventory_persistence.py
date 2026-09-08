# ruff: noqa: E501, I001
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from foundation.inventory import InventoryMovement
from foundation.inventory_persistence import DurableInventoryLedger
from foundation.product_information import ProductRecord
from foundation.product_persistence import DurableProductStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
UP = ("0001_operations_evidence.up.sql", "0002_foundation_durable_state.up.sql", "0003_durable_outbox_replay.up.sql", "0004_product_information.up.sql", "0005_catalog_assortment.up.sql", "0006_pricing_promotions.up.sql", "0007_inventory_ledger.up.sql")


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


def movement(movement_id: str, quantity: str, reversal: str | None = None) -> InventoryMovement:
    return InventoryMovement(movement_id, "tea", "store-1", "physical", Decimal(quantity), "test movement", NOW, reversal)


def test_durable_movements_replay_to_reconciled_positions(database: psycopg.Connection[object]) -> None:
    DurableProductStore(database).create(ProductRecord("tea", "tenant-a", "tea", "each", "active", ("tea",), ()), "product-trace")
    store = DurableInventoryLedger(database)
    store.post("tenant-a", movement("receive-1", "5"), "receive-trace")
    store.post("tenant-a", movement("reverse-1", "-5", "receive-1"), "reverse-trace")
    assert store.positions("tenant-a")[0].quantity == Decimal("0")
    assert store.rebuild_positions("tenant-a", "replay-trace") == store.positions("tenant-a")
    with database.cursor() as cursor:
        cursor.execute("SELECT event_type, schema_version FROM durable_outbox_records WHERE event_type LIKE 'Inventory%' ORDER BY event_type")
        assert cursor.fetchall() == [("InventoryMoved", "v1"), ("InventoryMoved", "v1"), ("InventoryPositionRebuilt", "v1")]


def test_missing_product_rolls_back_movement_and_event(database: psycopg.Connection[object]) -> None:
    with pytest.raises(psycopg.Error):
        DurableInventoryLedger(database).post("tenant-a", movement("missing-product", "5"), "failed-trace")
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM inventory_movements")
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM durable_outbox_records")
        assert cursor.fetchone() == (0,)
