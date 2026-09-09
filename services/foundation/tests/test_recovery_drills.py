# ruff: noqa: I001
"""Integrated recovery drills for the Phase 1 release candidate."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from foundation.operations import OperationsEvidenceService
from foundation.persistence import DurableFoundationStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
UP_MIGRATIONS = (
    "0001_operations_evidence.up.sql",
    "0002_foundation_durable_state.up.sql",
    "0003_durable_outbox_replay.up.sql",
)
DOWN_MIGRATIONS = (
    "0003_durable_outbox_replay.down.sql",
    "0002_foundation_durable_state.down.sql",
    "0001_operations_evidence.down.sql",
)


@pytest.fixture
def database() -> Iterator[tuple[psycopg.Connection[object], str]]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for recovery drills")
    connection = psycopg.connect(database_url)
    for name in UP_MIGRATIONS:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    yield connection, database_url
    for name in DOWN_MIGRATIONS:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    connection.close()


@pytest.mark.integration
def test_database_restore_and_full_migration_rollback_preserve_tenant_isolation(
    database: tuple[psycopg.Connection[object], str],
) -> None:
    """Restore a saved tenant after a complete migration rollback and reapply cycle."""
    connection, database_url = database
    store = DurableFoundationStore(connection)
    tenant_a_backup = {"currency": "USD", "timezone": "America/New_York"}
    store.create_organization("tenant-a", "root-a", None, tenant_a_backup, "trace-backup-a")
    store.create_organization("tenant-b", "root-b", None, {"currency": "EUR"}, "trace-backup-b")

    for name in DOWN_MIGRATIONS:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()
    for name in UP_MIGRATIONS:
        with connection.cursor() as cursor:
            cursor.execute((MIGRATIONS / name).read_text())
        connection.commit()

    restored = DurableFoundationStore(connection)
    restored.create_organization("tenant-a", "root-a", None, tenant_a_backup, "trace-restore-a")
    assert restored.effective_settings("tenant-a", "root-a") == tenant_a_backup
    with pytest.raises(PermissionError):
        restored.effective_settings("tenant-b", "root-b")

    evidence = OperationsEvidenceService(database_url)
    try:
        result = evidence.record_restore_exercise(
            f"restore-{uuid4()}", "tenant-a", "trace-restore-a", True, True
        )
    finally:
        evidence.close()
    assert result.data_restored is True
    assert result.service_behavior_restored is True
