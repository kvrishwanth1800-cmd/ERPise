from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from foundation.communications_store import PostgresCommunicationStore, StoredCommunication


@dataclass
class FakeCursor:
    queries: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    rows: list[tuple[object, ...]] = field(default_factory=list)

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> None:
        self.queries.append((query, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


@dataclass
class FakeConnection:
    cursor_value: FakeCursor = field(default_factory=FakeCursor)
    commits: int = 0
    rollbacks: int = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def message() -> StoredCommunication:
    return StoredCommunication(
        "message-1",
        "tenant-a",
        "email",
        "customer@example.test",
        "receipt",
        "key-1",
        "accepted",
        1,
        "provider-1",
        None,
    )


def test_transition_and_outbox_commit_together() -> None:
    connection = FakeConnection()
    store = PostgresCommunicationStore(connection)
    with store.transaction() as cursor:
        store.write_message_transition_and_outbox(cursor, message(), "queued", "event-1", "trace-1")
    assert connection.commits == 1
    assert len(connection.cursor_value.queries) == 3


def test_transaction_rolls_back_on_failure() -> None:
    connection = FakeConnection()
    store = PostgresCommunicationStore(connection)
    with pytest.raises(RuntimeError):
        with store.transaction():
            raise RuntimeError("database failure")
    assert connection.rollbacks == 1


def test_recovery_is_tenant_scoped() -> None:
    connection = FakeConnection()
    connection.cursor_value.rows = [
        (
            "message-1",
            "tenant-a",
            "email",
            "customer@example.test",
            "receipt",
            "key-1",
            "retryable",
            1,
            None,
            "timeout",
        )
    ]
    store = PostgresCommunicationStore(connection)
    recovered = store.recover_active_for_tenant(connection.cursor(), "tenant-a")
    assert recovered == [
        StoredCommunication(
            "message-1",
            "tenant-a",
            "email",
            "customer@example.test",
            "receipt",
            "key-1",
            "retryable",
            1,
            None,
            "timeout",
        )
    ]
    assert connection.cursor_value.queries[0][1] == ("tenant-a",)


def test_reversible_migration_contains_policy_and_down_sections() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "0002_communications.sql"
    migration = migration_path.read_text()
    assert "communication_preferences" in migration
    assert "communication_webhook_receipts" in migration
    assert "-- DOWN" in migration
    assert "DROP TABLE communication_templates" in migration
