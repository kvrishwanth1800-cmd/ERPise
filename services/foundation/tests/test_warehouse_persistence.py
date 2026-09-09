# ruff: noqa: E501
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import psycopg
import pytest

from foundation.warehouse import TaskLine, TransferLine, WarehouseTask, WarehouseTransfer
from foundation.warehouse_persistence import DurableWarehouseStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
UP = tuple(f"{number:04d}_{name}.up.sql" for number, name in ((1, "operations_evidence"), (2, "foundation_durable_state"), (3, "durable_outbox_replay"), (4, "product_information"), (5, "catalog_assortment"), (6, "pricing_promotions"), (7, "inventory_ledger"), (8, "customer_consent_loyalty"), (9, "payment_orchestration"), (10, "counter_pos_register"), (11, "order_management"), (12, "supplier_contract_governance"), (13, "procurement_lifecycle"), (14, "warehouse_tasks_transfers")))


@pytest.fixture()
def database() -> Iterator[psycopg.Connection[object]]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = psycopg.connect(DATABASE_URL)
    for name in UP:
        connection.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection
    for name in reversed(UP):
        connection.execute((MIGRATIONS / name.replace(".up.sql", ".down.sql")).read_text())
        connection.commit()
    connection.close()


def test_task_and_transfer_are_durable_scoped_and_emit_outbox(database: psycopg.Connection[object]) -> None:
    store = DurableWarehouseStore(database)
    task = WarehouseTask("task-1", "tenant-a", "wh-1", "picking", (TaskLine("line-1", "sku-1", Decimal("2"), "bin-a"),), "in_progress", "worker", "worker", ("line-1",))
    transfer = WarehouseTransfer("transfer-1", "tenant-a", "wh-1", "bin-a", "wh-2", "bin-z", (TransferLine("line-1", "sku-1", Decimal("2"), Decimal("2"), Decimal("1")),), "partially_received", "one pending")
    store.save_task(task, "task-trace")
    store.save_transfer(transfer, "transfer-trace")
    assert store.task("tenant-a", "wh-1", "task-1") == task
    assert store.task("tenant-b", "wh-1", "task-1") is None
    assert store.task("tenant-a", "wh-2", "task-1") is None
    assert store.transfer("tenant-a", "wh-2", "transfer-1") == transfer
    assert store.transfer("tenant-a", "wh-3", "transfer-1") is None
    row = database.execute("SELECT count(*) FROM durable_outbox_records WHERE tenant_id = 'tenant-a' AND event_type IN ('WarehouseTaskChanged', 'TransferChanged')").fetchone()
    assert cast(tuple[int], row)[0] == 2


def test_migration_constraints_reject_cross_route_and_invalid_state(database: psycopg.Connection[object]) -> None:
    with pytest.raises(psycopg.Error):
        database.execute("INSERT INTO warehouse_transfers (tenant_id, transfer_id, source_warehouse_id, source_bin_id, destination_warehouse_id, destination_bin_id, status, lines) VALUES ('tenant-a', 'bad', 'wh-1', 'bin-a', 'wh-1', 'bin-a', 'draft', '[]')")
    database.rollback()
    with pytest.raises(psycopg.Error):
        database.execute("INSERT INTO warehouse_tasks (tenant_id, task_id, warehouse_id, task_type, status, details) VALUES ('tenant-a', 'bad', 'wh-1', 'delivery', 'open', '{}')")
    database.rollback()
