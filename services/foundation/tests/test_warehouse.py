# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from foundation.access import AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger
from foundation.organization import ScopeContext
from foundation.warehouse import StockLot, TaskException, TaskLine, TransferLine, WarehouseService, WarehouseTask, WarehouseTransfer, WarehouseValidationError

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def service() -> tuple[WarehouseService, InventoryLedger, AuditRecorder]:
    access = AuthorizationService(SessionRevocationService())
    for permission in ("warehouse.write", "inventory.write"):
        access.grant(PermissionGrant("worker", "tenant-a", permission))
    audit = AuditRecorder()
    ledger = InventoryLedger(access, audit)
    return WarehouseService(access, audit, ledger), ledger, audit


def task(task_type: str = "picking", allow_substitution: bool = False) -> WarehouseTask:
    return WarehouseTask("task-1", "tenant-a", "wh-1", task_type, (TaskLine("line-1", "sku-1", Decimal("5"), "bin-a", "bin-b", allow_substitution),))


def claim(subject: WarehouseService, value: WarehouseTask | None = None) -> WarehouseTask:
    subject.create_task("worker", "session", scope(), value or task(), "create")
    return subject.claim("worker", "session", scope(), "task-1", "claim")


def test_assigned_worker_scan_uses_fefo_and_completes() -> None:
    subject, _, audit = service()
    claim(subject)
    selected = subject.scan("worker", "session", scope(), "task-1", "line-1", "sku-1", "bin-a", (StockLot("sku-1", "wh-1", "bin-a", Decimal("5"), "later", date(2027, 2, 1)), StockLot("sku-1", "wh-1", "bin-a", Decimal("5"), "first", date(2027, 1, 1))), "scan")
    completed = subject.complete_task("worker", "session", scope(), "task-1", NOW, "complete")
    assert selected.batch_id == "first"
    assert completed.status == "completed"
    assert [event.event_type for event in subject.outbox][-1] == "WarehouseTaskChanged"
    assert audit.records[-1].source == "task.complete"


def test_scan_rejects_wrong_task_product_bin_and_warehouse_stock() -> None:
    subject, _, _ = service()
    claim(subject)
    with pytest.raises(WarehouseValidationError, match="does not match"):
        subject.scan("worker", "session", scope(), "task-1", "line-1", "sku-2", "bin-a", (), "scan")
    with pytest.raises(WarehouseValidationError, match="no eligible stock"):
        subject.scan("worker", "session", scope(), "task-1", "line-1", "sku-1", "bin-a", (StockLot("sku-1", "wh-2", "bin-a", Decimal("5")),), "scan")


def test_short_or_substitute_exception_is_required_before_completion() -> None:
    subject, _, _ = service()
    claim(subject, task(allow_substitution=True))
    with pytest.raises(WarehouseValidationError, match="every task line"):
        subject.complete_task("worker", "session", scope(), "task-1", NOW, "complete")
    subject.record_exception("worker", "session", scope(), "task-1", TaskException("ex-1", "line-1", "substitute", Decimal("5"), "approved replacement", "sku-2", NOW), "exception")
    completed = subject.complete_task("worker", "session", scope(), "task-1", NOW, "complete")
    assert completed.status == "completed"
    assert subject.shipment_ready(scope(), ("task-1",))


def test_assignment_claim_cancel_authorization_and_tenant_isolation() -> None:
    subject, _, _ = service()
    subject.create_task("worker", "session", scope(), task(), "create")
    subject.assign("worker", "session", scope(), "task-1", "other", "assign")
    with pytest.raises(WarehouseValidationError, match="another worker"):
        subject.claim("worker", "session", scope(), "task-1", "claim")
    assert subject.task(scope("tenant-b"), "task-1") is None
    assert subject.cancel_task("worker", "session", scope(), "task-1", "order cancelled", "cancel").status == "cancelled"


def test_internal_movement_posts_balanced_bin_stock() -> None:
    subject, ledger, _ = service()
    claim(subject, task("internal_movement"))
    subject.scan("worker", "session", scope(), "task-1", "line-1", "sku-1", "bin-a", (StockLot("sku-1", "wh-1", "bin-a", Decimal("5")),), "scan")
    subject.complete_task("worker", "session", scope(), "task-1", NOW, "complete")
    positions = {(item.location_id, item.quantity) for item in ledger.positions(scope())}
    assert positions == {("bin-a", Decimal("-5")), ("bin-b", Decimal("5"))}


def test_transfer_preserves_in_transit_and_reconciles_partial_receipt() -> None:
    subject, ledger, _ = service()
    transfer = WarehouseTransfer("transfer-1", "tenant-a", "wh-1", "bin-a", "wh-2", "bin-z", (TransferLine("line-1", "sku-1", Decimal("10")),))
    subject.create_transfer("worker", "session", scope(), transfer, "create")
    sent = subject.send_transfer("worker", "session", scope(), "transfer-1", {"line-1": Decimal("10")}, NOW, "send")
    partial = subject.receive_transfer("worker", "session", scope(), "transfer-1", {"line-1": Decimal("6")}, NOW, "receive-1", "four units pending")
    received = subject.receive_transfer("worker", "session", scope(), "transfer-1", {"line-1": Decimal("4")}, NOW, "receive-2")
    assert sent.status == "in_transit"
    assert partial.status == "partially_received"
    assert partial.discrepancy_reason == "four units pending"
    assert received.status == "received"
    positions = {(item.location_id, item.quantity_type): item.quantity for item in ledger.positions(scope())}
    assert positions[("bin-a", "physical")] == Decimal("-10")
    assert positions[("bin-z", "physical")] == Decimal("10")
    assert positions[("wh-2", "in_transit")] == Decimal("0")
