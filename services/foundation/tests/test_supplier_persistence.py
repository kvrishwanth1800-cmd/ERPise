# ruff: noqa: E501
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from foundation.supplier import (
    BankChangeRequest,
    BankDetails,
    Certification,
    ContractVersion,
    SupplierRecord,
)
from foundation.supplier_persistence import DurableSupplierStore

MIGRATIONS = Path(__file__).parents[1] / "migrations"
DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
TODAY = date(2026, 9, 8)
BANK = BankDetails("Acme Supplies", "12345678", "BANKGB2L")
UP = tuple(
    f"{number:04d}_{name}.up.sql"
    for number, name in (
        (1, "operations_evidence"),
        (2, "foundation_durable_state"),
        (3, "durable_outbox_replay"),
        (4, "product_information"),
        (5, "catalog_assortment"),
        (6, "pricing_promotions"),
        (7, "inventory_ledger"),
        (8, "customer_consent_loyalty"),
        (9, "payment_orchestration"),
        (10, "counter_pos_register"),
        (11, "order_management"),
        (12, "supplier_contract_governance"),
    )
)


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


def supplier(supplier_id: str = "supplier-1", tenant_id: str = "tenant-a") -> SupplierRecord:
    return SupplierRecord(supplier_id, tenant_id, "Acme Supplies", "active", "org-north")


def bank_request(
    request_id: str = "bank-1",
    idempotency_key: str = "idem-bank-1",
    requester_id: str = "buyer",
    tenant_id: str = "tenant-a",
) -> BankChangeRequest:
    return BankChangeRequest(
        request_id, tenant_id, "supplier-1", requester_id, BANK, idempotency_key, NOW
    )


def version(
    version_id: str,
    number: int,
    terms: dict[str, str],
    effective_from: datetime,
    expires_on: date | None = None,
    tenant_id: str = "tenant-a",
) -> ContractVersion:
    return ContractVersion(
        version_id, tenant_id, "contract-1", "supplier-1", number, terms, effective_from, expires_on
    )


def test_bank_change_is_inactive_until_a_separate_approver_approves_it(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.request_bank_change(bank_request())

    assert store.active_bank_details("tenant-a", "supplier-1") is None
    store.approve_bank_change("tenant-a", "bank-1", "buyer", NOW, "self-trace")
    assert store.active_bank_details("tenant-a", "supplier-1") is None

    store.approve_bank_change("tenant-a", "bank-1", "controller", NOW, "approve-trace")
    assert store.active_bank_details("tenant-a", "supplier-1") == BANK
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT status, approver_id FROM supplier_bank_change_requests WHERE tenant_id = 'tenant-a' AND request_id = 'bank-1'"
        )
        assert cursor.fetchone() == ("approved", "controller")


def test_bank_change_requests_are_idempotent_per_tenant(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.request_bank_change(bank_request())
    store.request_bank_change(bank_request("bank-2", "idem-bank-1"))

    with database.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM supplier_bank_change_requests WHERE tenant_id = 'tenant-a'"
        )
        assert cursor.fetchone() == (1,)


def test_a_requester_cannot_be_stored_as_their_own_approver(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.request_bank_change(bank_request())

    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="supplier_bank_change_separate_approver"):
            cursor.execute(
                "UPDATE supplier_bank_change_requests SET status = 'approved', approver_id = 'buyer', "
                "decided_at = now() WHERE tenant_id = 'tenant-a' AND request_id = 'bank-1'"
            )
        database.rollback()


def test_contract_versions_are_immutable_and_prior_terms_survive_activation(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.register_contract_version(
        version("version-1", 1, {"payment_terms": "net-30"}, NOW - timedelta(days=10)), "register-1"
    )
    store.register_contract_version(
        version("version-2", 2, {"payment_terms": "net-45"}, NOW), "register-2"
    )
    store.activate_contract_version("tenant-a", "version-1", "activate-1")
    store.activate_contract_version("tenant-a", "version-2", "activate-2")

    assert store.effective_terms("tenant-a", "contract-1", NOW) == {"payment_terms": "net-45"}
    assert store.effective_terms("tenant-a", "contract-1", NOW - timedelta(days=5)) == {
        "payment_terms": "net-30"
    }
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM supplier_contract_versions WHERE tenant_id = 'tenant-a' AND version_id = 'version-1'"
        )
        assert cursor.fetchone() == ("superseded",)
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute(
                "UPDATE supplier_contract_versions SET terms = '{}'::jsonb WHERE version_id = 'version-1'"
            )
        database.rollback()
    with database.cursor() as cursor:
        with pytest.raises(psycopg.Error, match="append-only"):
            cursor.execute("DELETE FROM supplier_contract_versions WHERE version_id = 'version-1'")
        database.rollback()


def test_duplicate_contract_version_numbers_are_rejected_by_the_database(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.register_contract_version(
        version("version-1", 1, {"payment_terms": "net-30"}, NOW), "register-1"
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        store.register_contract_version(
            version("version-duplicate", 1, {"payment_terms": "net-60"}, NOW + timedelta(days=1)),
            "register-duplicate",
        )
    database.rollback()


def test_approaching_expiries_are_advised_to_the_responsible_scope(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.register_certification(
        Certification("cert-1", "tenant-a", "supplier-1", "iso-9001", TODAY + timedelta(days=10)),
        "cert-trace",
    )
    store.register_certification(
        Certification("cert-2", "tenant-a", "supplier-1", "insurance", TODAY + timedelta(days=400)),
        "cert-trace-2",
    )
    store.register_contract_version(
        version("version-1", 1, {"payment_terms": "net-30"}, NOW, TODAY + timedelta(days=20)),
        "register-1",
    )
    store.activate_contract_version("tenant-a", "version-1", "activate-1")

    advisories = store.advise_expiries(
        "tenant-a", TODAY, TODAY + timedelta(days=30), "expiry-trace"
    )
    assert [(advisory[0], advisory[1], advisory[2]) for advisory in advisories] == [
        ("certification", "cert-1", "org-north"),
        ("contract", "version-1", "org-north"),
    ]
    with database.cursor() as cursor:
        cursor.execute(
            "SELECT event_type, count(*) FROM durable_outbox_records WHERE tenant_id = 'tenant-a' "
            "GROUP BY event_type ORDER BY event_type"
        )
        assert cursor.fetchall() == [("ContractActivated", 1), ("SupplierChanged", 3)]
        cursor.execute(
            "SELECT count(*) FROM audit_records WHERE tenant_id = 'tenant-a' AND source = 'outbox.commit'"
        )
        assert cursor.fetchone() == (4,)


def test_supplier_facts_publish_events_and_audit_evidence(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.request_bank_change(bank_request())
    store.approve_bank_change("tenant-a", "bank-1", "controller", NOW, "approve-trace")
    store.register_contract_version(
        version("version-1", 1, {"payment_terms": "net-30"}, NOW), "register-1"
    )
    store.activate_contract_version("tenant-a", "version-1", "activate-1")

    with database.cursor() as cursor:
        cursor.execute(
            "SELECT event_type, count(*) FROM durable_outbox_records WHERE tenant_id = 'tenant-a' "
            "GROUP BY event_type ORDER BY event_type"
        )
        assert cursor.fetchall() == [("ContractActivated", 1), ("SupplierChanged", 3)]
        cursor.execute(
            "SELECT count(*) FROM audit_records WHERE tenant_id = 'tenant-a' AND source = 'outbox.commit'"
        )
        assert cursor.fetchone() == (4,)


def test_supplier_records_are_scoped_to_their_tenant(
    database: psycopg.Connection[object],
) -> None:
    store = DurableSupplierStore(database)
    store.register_supplier(supplier(), "register-trace")
    store.register_supplier(supplier("supplier-2", "tenant-b"), "register-trace-b")

    assert store.active_bank_details("tenant-b", "supplier-1") is None
    assert store.effective_terms("tenant-b", "contract-1", NOW) is None
    with database.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM suppliers WHERE tenant_id = 'tenant-b'")
        assert cursor.fetchone() == (1,)
