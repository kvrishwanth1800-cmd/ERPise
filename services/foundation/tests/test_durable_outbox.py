"""Integration coverage for PostgreSQL outbox delivery and replay recovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from foundation.durable_outbox import DurableEvent, DurableOutboxStore, EventBroker

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


class RecordingBroker(EventBroker):
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.events: list[str] = []

    def publish(self, event: DurableEvent) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("broker unavailable")
        self.events.append(event.event_id)


@pytest.fixture()
def database() -> Iterator[psycopg.Connection[object]]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = psycopg.connect(DATABASE_URL)
    for name in (
        "0001_operations_evidence.up.sql",
        "0002_foundation_durable_state.up.sql",
        "0003_durable_outbox_replay.up.sql",
    ):
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection
    for name in (
        "0003_durable_outbox_replay.down.sql",
        "0002_foundation_durable_state.down.sql",
        "0001_operations_evidence.down.sql",
    ):
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    connection.close()


def event(event_id: str, tenant_id: str = "tenant-a") -> DurableEvent:
    return DurableEvent(
        event_id, tenant_id, "OrganizationChanged", "v1", "trace-1", {"id": event_id}, datetime.now(UTC)
    )


def write_record(cursor: psycopg.Cursor[object], event_id: str, tenant_id: str = "tenant-a") -> None:
    cursor.execute(
        "INSERT INTO outbox_business_records (record_id, tenant_id, value) VALUES (%s, %s, 'changed')",
        (event_id, tenant_id),
    )


def test_committed_business_state_and_outbox_are_atomic(database: psycopg.Connection[object]) -> None:
    store = DurableOutboxStore(database)
    store.commit_business_event(event("event-1"), lambda cursor: write_record(cursor, "event-1"))
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM outbox_business_records")
        assert cursor.fetchone() == (1,)
        cursor.execute("SELECT count(*) FROM durable_outbox_records")
        assert cursor.fetchone() == (1,)


def test_failed_business_write_loses_neither_partial_state_nor_event(database: psycopg.Connection[object]) -> None:
    store = DurableOutboxStore(database)

    def fail(cursor: psycopg.Cursor[object]) -> None:
        write_record(cursor, "event-rollback")
        raise RuntimeError("crash before commit")

    with pytest.raises(RuntimeError, match="crash"):
        store.commit_business_event(event("event-rollback"), fail)
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM durable_outbox_records")
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM outbox_business_records")
        assert cursor.fetchone() == (0,)


def test_failed_delivery_recovers_after_restart(database: psycopg.Connection[object]) -> None:
    store = DurableOutboxStore(database)
    store.commit_business_event(event("event-retry"), lambda cursor: write_record(cursor, "event-retry"))
    failing = RecordingBroker(failures=1)
    assert store.publish_pending(failing) == ()
    recovered = DurableOutboxStore(database)
    broker = RecordingBroker()
    assert recovered.publish_pending(broker) == ("event-retry",)
    assert broker.events == ["event-retry"]


def test_duplicate_and_out_of_order_delivery_have_one_logical_effect(database: psycopg.Connection[object]) -> None:
    store = DurableOutboxStore(database)
    for event_id in ("event-1", "event-2"):
        store.commit_business_event(event(event_id), lambda cursor, key=event_id: write_record(cursor, key))
    delivered = store.pending()
    effects: list[str] = []
    assert store.consume("inventory", delivered[1], "inventory", lambda item: effects.append(item.event_id))
    assert store.consume("inventory", delivered[0], "inventory", lambda item: effects.append(item.event_id))
    assert not store.consume("inventory", delivered[0], "inventory", lambda item: effects.append(item.event_id))
    assert effects == ["event-2", "event-1"]
    assert store.projection_count("inventory", "tenant-a", "inventory") == 2


def test_replay_rebuilds_projection_without_external_effects(database: psycopg.Connection[object]) -> None:
    store = DurableOutboxStore(database)
    for event_id in ("event-1", "event-2"):
        store.commit_business_event(event(event_id), lambda cursor, key=event_id: write_record(cursor, key))
    unsafe_effects: list[str] = []
    events = store.pending()
    store.consume("ledger", events[0], "ledger", lambda item: unsafe_effects.append(item.event_id))
    assert store.replay("ledger", "tenant-a", events, "ledger") == (True, True)
    assert unsafe_effects == ["event-1"]
    assert store.projection_count("ledger", "tenant-a", "ledger") == 2


def test_dead_letter_and_tenant_isolation_are_durable(database: psycopg.Connection[object]) -> None:
    store = DurableOutboxStore(database)
    store.commit_business_event(event("event-dead"), lambda cursor: write_record(cursor, "event-dead"))
    assert store.publish_pending(RecordingBroker(failures=3), max_attempts=1) == ()
    with database.cursor() as cursor:
        cursor.execute("SELECT tenant_id FROM outbox_dead_letters")
        assert cursor.fetchone() == ("tenant-a",)
    with pytest.raises(PermissionError):
        store.consume("inventory", event("event-dead", "tenant-b"), "inventory", lambda _: None)


def test_outbox_migration_rolls_back_and_restores(database: psycopg.Connection[object]) -> None:
    with database.cursor() as cursor:
        cursor.execute((MIGRATIONS / "0003_durable_outbox_replay.down.sql").read_text())
        cursor.execute((MIGRATIONS / "0003_durable_outbox_replay.up.sql").read_text())
        cursor.execute("SELECT to_regclass('public.consumer_event_progress')")
        assert cursor.fetchone() == ("consumer_event_progress",)
