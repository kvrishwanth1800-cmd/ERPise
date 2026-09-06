# ruff: noqa: E501, I001
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from foundation.product_information import ProductRecord
from foundation.product_persistence import DurableProductStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def database() -> Iterator[psycopg.Connection[object]]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = psycopg.connect(DATABASE_URL)
    for name in (
        "0001_operations_evidence.up.sql",
        "0002_foundation_durable_state.up.sql",
        "0003_durable_outbox_replay.up.sql",
        "0004_product_information.up.sql",
    ):
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection
    for name in (
        "0004_product_information.down.sql",
        "0003_durable_outbox_replay.down.sql",
        "0002_foundation_durable_state.down.sql",
        "0001_operations_evidence.down.sql",
    ):
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    connection.close()


def record(product_id: str = "product-1", identifier: str = "barcode-1") -> ProductRecord:
    return ProductRecord(product_id, "tenant-a", "Tea", "each", "draft", (identifier,), ("variant-1",))


def test_product_write_event_and_audit_are_atomic(database: psycopg.Connection[object]) -> None:
    DurableProductStore(database).create(record(), "trace-1")
    with database.cursor() as cursor:
        cursor.execute("SELECT name FROM products WHERE tenant_id = 'tenant-a' AND product_id = 'product-1'")
        assert cursor.fetchone() == ("Tea",)
        cursor.execute("SELECT event_type FROM durable_outbox_records")
        assert cursor.fetchone() == ("ProductChanged",)
        cursor.execute("SELECT result FROM audit_records WHERE trace_id = 'trace-1'")
        assert cursor.fetchone() == ("committed",)


def test_identifier_unique_within_tenant_and_migration_rolls_back(database: psycopg.Connection[object]) -> None:
    store = DurableProductStore(database)
    store.create(record(), "trace-1")
    with pytest.raises(psycopg.Error):
        store.create(record("product-2"), "trace-2")
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM products")
        assert cursor.fetchone() == (1,)
        cursor.execute("SELECT count(*) FROM durable_outbox_records")
        assert cursor.fetchone() == (1,)
