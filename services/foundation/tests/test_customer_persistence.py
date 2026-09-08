from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from foundation.customer import ConsentFact, Customer, PrivacyRequest, StoredValueEffect
from foundation.customer_persistence import DurableCustomerConsentStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
UP = tuple(f"{number:04d}_{name}.up.sql" for number, name in ((1, "operations_evidence"), (2, "foundation_durable_state"), (3, "durable_outbox_replay"), (4, "product_information"), (5, "catalog_assortment"), (6, "pricing_promotions"), (7, "inventory_ledger"), (8, "customer_consent_loyalty")))


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


def test_customer_facts_events_and_reversal_are_durable_and_tenant_scoped(database: psycopg.Connection[object]) -> None:
    store = DurableCustomerConsentStore(database)
    store.create_customer(Customer("c1", "tenant-a"), "customer-trace")
    store.record_consent("tenant-a", ConsentFact("grant", "c1", "marketing", "granted", "v1", "signed", "email", NOW), "consent-trace")
    store.complete_privacy_request("tenant-a", PrivacyRequest("export", "c1", "export", "profile", "verified", "exported"), "privacy-trace")
    store.post_value("tenant-a", StoredValueEffect("credit", "c1", "credit", Decimal("10"), "goodwill", NOW), "value-trace")
    store.post_value("tenant-a", StoredValueEffect("reversal", "c1", "credit", Decimal("-10"), "correction", NOW, "credit"), "reverse-trace")

    assert store.balance("tenant-a", "c1", "credit") == Decimal("0")
    with database.cursor() as cursor:
        cursor.execute("SELECT event_type FROM durable_outbox_records WHERE tenant_id = 'tenant-a' ORDER BY event_type")
        assert cursor.fetchall() == [("ConsentChanged",), ("CustomerChanged",), ("CustomerChanged",), ("StoredValueChanged",), ("StoredValueChanged",)]
        cursor.execute("SELECT count(*) FROM consent_facts WHERE tenant_id = 'tenant-b'")
        assert cursor.fetchone() == (0,)
