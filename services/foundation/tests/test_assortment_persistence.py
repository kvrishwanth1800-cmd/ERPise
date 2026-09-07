from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from foundation.assortment import AssortmentRecord, AssortmentScope
from foundation.assortment_persistence import DurableAssortmentStore
from foundation.product_information import ProductRecord
from foundation.product_persistence import DurableProductStore


MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


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
    )
    down = tuple(name.replace(".up.sql", ".down.sql") for name in reversed(up))
    for name in up:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection
    for name in down:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    connection.close()


def product(product_id: str) -> ProductRecord:
    return ProductRecord(
        product_id,
        "tenant-a",
        product_id,
        "each",
        "active",
        (f"id-{product_id}",),
        (),
    )


def assortment(assortment_id: str, products: tuple[str, ...]) -> AssortmentRecord:
    return AssortmentRecord(
        assortment_id,
        "tenant-a",
        AssortmentScope(store_id="shop-1", channel_id="web", segment_id="vip"),
        products,
        NOW,
        None,
    )


def test_published_assortment_and_event_are_durable(
    database: psycopg.Connection[object],
) -> None:
    DurableProductStore(database).create(product("tea"), "product-trace")
    store = DurableAssortmentStore(database)
    store.publish(assortment("vip", ("tea",)), "assortment-trace")

    result = store.eligibility(
        "tenant-a",
        AssortmentScope(store_id="shop-1", channel_id="web", segment_id="vip"),
        NOW,
    )

    assert result.assortment_id == "vip"
    assert result.product_ids == ("tea",)
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT event_type FROM durable_outbox_records "
            "WHERE trace_id = 'assortment-trace'"
        )
        assert cursor.fetchone() == ("AssortmentPublished",)


def test_assortment_insert_rolls_back_state_and_event_on_bad_product(
    database: psycopg.Connection[object],
) -> None:
    store = DurableAssortmentStore(database)
    with pytest.raises(psycopg.Error):
        store.publish(assortment("missing", ("missing-product",)), "failed-trace")

    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM assortments")
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM durable_outbox_records")
        assert cursor.fetchone() == (0,)
