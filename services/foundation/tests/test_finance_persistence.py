# ruff: noqa: E501, I001
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import psycopg
import pytest

from foundation.finance import Account, FinancialPeriod, JournalEntry, JournalLine
from foundation.finance_persistence import DurableFinancialStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
UP = tuple(f"{number:04d}_{name}.up.sql" for number, name in ((1, "operations_evidence"), (2, "foundation_durable_state"), (3, "durable_outbox_replay"), (4, "product_information"), (5, "catalog_assortment"), (6, "pricing_promotions"), (7, "inventory_ledger"), (8, "customer_consent_loyalty"), (9, "payment_orchestration"), (10, "counter_pos_register"), (11, "order_management"), (12, "supplier_contract_governance"), (13, "procurement_lifecycle"), (14, "warehouse_tasks_transfers"), (15, "financial_posting_reconciliation"), (16, "financial_posting_evidence")))


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


def test_financial_journal_is_durable_idempotent_and_emits_tenant_event(database: psycopg.Connection[object]) -> None:
    store = DurableFinancialStore(database)
    store.save_account(Account("ar", "tenant-a", "1100", "Accounts receivable", "asset", "USD"), "account-ar")
    store.save_account(Account("revenue", "tenant-a", "4000", "Sales", "revenue", "USD"), "account-revenue")
    store.save_period(FinancialPeriod("p1", "tenant-a", "entity-a", date(2026, 9, 1), date(2026, 9, 30)), "period")
    journal = JournalEntry("j1", "tenant-a", "entity-a", "p1", "USD", (JournalLine("ar", debit=Decimal("12.34")), JournalLine("revenue", credit=Decimal("12.34"))), "sale", "order-1", "trace-1", datetime(2026, 9, 9, tzinfo=UTC))
    store.post_journal(journal)
    store.post_journal(journal)
    row = database.execute("SELECT count(*) FROM journal_entries WHERE tenant_id = 'tenant-a' AND source_id = 'order-1'").fetchone()
    assert cast(tuple[int], row)[0] == 1
    row = database.execute("SELECT count(*) FROM durable_outbox_records WHERE tenant_id = 'tenant-a' AND event_type = 'JournalPosted'").fetchone()
    assert cast(tuple[int], row)[0] == 1
