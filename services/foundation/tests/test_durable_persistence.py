"""PostgreSQL integration checks for RWO-2 durable foundation state."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Barrier, Thread

import psycopg
import pytest

from foundation.persistence import DurableFoundationStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def database() -> psycopg.Connection[object]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    connection = psycopg.connect(DATABASE_URL)
    with connection.cursor() as cursor:
        cursor.execute((MIGRATIONS / "0001_operations_evidence.up.sql").read_text())
        cursor.execute((MIGRATIONS / "0002_foundation_durable_state.up.sql").read_text())
    connection.commit()
    yield connection
    with connection.cursor() as cursor:
        cursor.execute((MIGRATIONS / "0002_foundation_durable_state.down.sql").read_text())
        cursor.execute((MIGRATIONS / "0001_operations_evidence.down.sql").read_text())
    connection.commit()
    connection.close()


def test_hierarchy_write_and_outbox_event_commit_together(
    database: psycopg.Connection[object],
) -> None:
    store = DurableFoundationStore(database)
    store.create_organization("tenant-a", "root", None, {"currency": "USD"}, "trace-1")

    with database.cursor() as cursor:
        cursor.execute("SELECT tenant_id FROM organizations WHERE organization_id = 'root'")
        assert cursor.fetchone() == ("tenant-a",)
        cursor.execute("SELECT action, trace_id FROM hierarchy_outbox_records")
        assert cursor.fetchone() == ("created", "trace-1")


def test_tenant_isolation_and_deterministic_settings(
    database: psycopg.Connection[object],
) -> None:
    store = DurableFoundationStore(database)
    store.create_organization("tenant-a", "root", None, {"currency": "USD"}, "trace-1")
    store.create_organization(
        "tenant-a", "store", "root", {"timezone": "America/New_York"}, "trace-2"
    )

    assert store.effective_settings("tenant-a", "store") == {
        "currency": "USD",
        "timezone": "America/New_York",
    }
    with pytest.raises(PermissionError):
        store.effective_settings("tenant-b", "store")


def test_failed_hierarchy_write_rolls_back_its_outbox_record(
    database: psycopg.Connection[object],
) -> None:
    store = DurableFoundationStore(database)
    with pytest.raises(psycopg.Error):
        store.create_organization("tenant-a", "child", "missing-parent", {}, "trace-failure")

    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM organizations")
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM hierarchy_outbox_records")
        assert cursor.fetchone() == (0,)


def test_audit_is_append_only_and_authorization_grants_are_durable(
    database: psycopg.Connection[object],
) -> None:
    store = DurableFoundationStore(database)
    store.grant(
        "grant-1",
        "principal-1",
        "tenant-a",
        "order.read",
        {"store"},
        {"order-1"},
    )
    audit_id = store.record_audit(
        "tenant-a",
        "principal-1",
        "role",
        "order",
        "read",
        "policy",
        "trace-a",
        "allowed",
    )

    with database.cursor() as cursor:
        cursor.execute("SELECT tenant_id FROM authorization_grants WHERE grant_id = 'grant-1'")
        assert cursor.fetchone() == ("tenant-a",)
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("UPDATE audit_records SET result = 'changed' WHERE audit_id = %s", (audit_id,))
        database.rollback()
        cursor.execute("SELECT result FROM audit_records WHERE audit_id = %s", (audit_id,))
        assert cursor.fetchone() == ("allowed",)


def test_organization_delete_is_protected_by_operational_dependency(
    database: psycopg.Connection[object],
) -> None:
    store = DurableFoundationStore(database)
    store.create_organization("tenant-a", "root", None, {}, "trace-1")
    with database.cursor() as cursor:
        cursor.execute(
            "INSERT INTO organization_operational_dependencies "
            "(dependency_id, tenant_id, organization_id) "
            "VALUES ('dep-1', 'tenant-a', 'root')"
        )
    database.commit()

    with pytest.raises(psycopg.Error, match="protected dependencies"):
        store.delete_organization("tenant-a", "root", "trace-delete")


def test_concurrent_creates_keep_one_organization_and_one_outbox_event(
    database: psycopg.Connection[object],
) -> None:
    assert DATABASE_URL
    barrier = Barrier(2)
    outcomes: list[str] = []

    def create_from_connection() -> None:
        connection = psycopg.connect(DATABASE_URL)
        try:
            barrier.wait()
            DurableFoundationStore(connection).create_organization(
                "tenant-a", "root", None, {}, "trace-concurrent"
            )
            outcomes.append("created")
        except psycopg.Error:
            outcomes.append("rejected")
        finally:
            connection.close()

    first = Thread(target=create_from_connection)
    second = Thread(target=create_from_connection)
    first.start()
    second.start()
    first.join()
    second.join()

    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM organizations WHERE organization_id = 'root'")
        assert cursor.fetchone() == (1,)
        cursor.execute(
            "SELECT count(*) FROM hierarchy_outbox_records WHERE organization_id = 'root'"
        )
        assert cursor.fetchone() == (1,)
    assert sorted(outcomes) == ["created", "rejected"]


def test_down_then_up_migration_restores_schema(database: psycopg.Connection[object]) -> None:
    with database.cursor() as cursor:
        cursor.execute((MIGRATIONS / "0002_foundation_durable_state.down.sql").read_text())
        cursor.execute((MIGRATIONS / "0002_foundation_durable_state.up.sql").read_text())
        cursor.execute("SELECT to_regclass('public.organizations')")
        assert cursor.fetchone() == ("organizations",)
