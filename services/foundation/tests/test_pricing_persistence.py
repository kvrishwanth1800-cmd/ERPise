from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from foundation.pricing import CouponAlreadyCommittedError, PriceEntry, PriceScope, Promotion
from foundation.pricing_persistence import DurablePricingStore
from foundation.product_information import ProductRecord
from foundation.product_persistence import DurableProductStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


@pytest.fixture()
def database() -> Iterator[psycopg.Connection[object]]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = psycopg.connect(DATABASE_URL)
    up = (
        "0001_operations_evidence.up.sql",
        "0002_foundation_durable_state.up.sql",
        "0003_durable_outbox_replay.up.sql",
        "0004_product_information.up.sql",
        "0005_catalog_assortment.up.sql",
        "0006_pricing_promotions.up.sql",
    )
    for name in up:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection
    for name in reversed(up):
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name.replace(".up.sql", ".down.sql")).read_text())
        connection.commit()
    connection.close()


def product(product_id: str) -> ProductRecord:
    return ProductRecord(product_id, "tenant-a", product_id, "each", "active", ("tea",), ())


def price(price_list_id: str = "retail") -> PriceEntry:
    return PriceEntry(price_list_id, "tea", PriceScope(currency="USD"), Decimal("12.50"), NOW)


def promotion() -> Promotion:
    return Promotion("vip", "tea", PriceScope(currency="USD"), Decimal("10"), 1, True, NOW)


def test_pricing_wildcard_scope_and_events_are_durable(database: psycopg.Connection[object]) -> None:
    DurableProductStore(database).create(product("tea"), "product-trace")
    store = DurablePricingStore(database)
    store.publish_price("tenant-a", price(), "price-trace")
    store.publish_promotion("tenant-a", promotion(), "promotion-trace")

    with database.cursor() as cursor:
        cursor.execute("SELECT store_id, channel_id, segment_id FROM price_entries")
        assert cursor.fetchone() == (None, None, None)
        cursor.execute("SELECT event_type, schema_version FROM durable_outbox_records ORDER BY event_type")
        assert cursor.fetchall() == [("PricePublished", "v1"), ("PromotionPublished", "v1")]


def test_coupon_commit_is_idempotent_only_for_the_same_quote(database: psycopg.Connection[object]) -> None:
    store = DurablePricingStore(database)
    store.commit_coupon("tenant-a", "SAVE10", "tea|USD|10.00|retail", "coupon-one")
    store.commit_coupon("tenant-a", "SAVE10", "tea|USD|10.00|retail", "coupon-two")

    with pytest.raises(CouponAlreadyCommittedError):
        store.commit_coupon("tenant-a", "SAVE10", "tea|USD|9.00|retail", "coupon-three")

    with database.cursor() as cursor:
        cursor.execute("SELECT quote_key FROM coupon_commits")
        assert cursor.fetchone() == ("tea|USD|10.00|retail",)
        cursor.execute("SELECT count(*) FROM durable_outbox_records WHERE event_type = 'CouponCommitted'")
        assert cursor.fetchone() == (1,)


def test_price_insert_rolls_back_fact_and_event_on_missing_product(database: psycopg.Connection[object]) -> None:
    with pytest.raises(psycopg.Error):
        DurablePricingStore(database).publish_price("tenant-a", price(), "failed-price")

    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM price_entries")
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM durable_outbox_records")
        assert cursor.fetchone() == (0,)
