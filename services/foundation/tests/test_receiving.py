# ruff: noqa: E501, I001
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from foundation.access import AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger
from foundation.organization import ScopeContext
from foundation.receiving import ReceiptCommand, ReceiptValidationError, ReceivingService

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def service() -> tuple[ReceivingService, InventoryLedger, AuditRecorder]:
    access = AuthorizationService(SessionRevocationService())
    access.grant(PermissionGrant("receiver", "tenant-a", "receiving.write"))
    access.grant(PermissionGrant("receiver", "tenant-a", "inventory.write"))
    audit = AuditRecorder()
    ledger = InventoryLedger(access, audit)
    return ReceivingService(access, audit, ledger), ledger, audit


def receipt(receipt_id: str = "r-1", accepted: str = "5", expected: str | None = "5") -> ReceiptCommand:
    return ReceiptCommand(receipt_id, "tea", "dock-1", "store-1", Decimal(accepted), None if expected is None else Decimal(expected), Decimal("7.50"), "batch-7", "serial-7", date(2027, 1, 1), NOW, "purchase order received")


def test_receipt_captures_evidence_and_requests_putaway_before_stock_effect() -> None:
    subject, ledger, audit = service()
    outcome = subject.receive("receiver", "session", scope(), receipt(), "trace")
    assert outcome.receipt.unit_cost == Decimal("7.50")
    assert outcome.receipt.batch_id == "batch-7"
    assert outcome.receipt.serial_id == "serial-7"
    assert outcome.receipt.expires_on == date(2027, 1, 1)
    assert ledger.positions(scope())[0].quantity == Decimal("5")
    assert [event.event_type for event in subject.outbox] == ["ReceiptRecorded", "PutawayRequested"]
    assert audit.records[-1].source == "receipt.record"


def test_discrepancy_is_recorded_before_receipt_completion() -> None:
    subject, _, audit = service()
    outcome = subject.receive("receiver", "session", scope(), receipt(accepted="3", expected="5"), "trace")
    assert outcome.discrepancy_quantity == Decimal("-2")
    assert [record.source for record in audit.records][-2:] == ["receipt.discrepancy", "receipt.record"]


def test_duplicate_submission_has_one_logical_outcome_and_one_stock_effect() -> None:
    subject, ledger, _ = service()
    first = subject.receive("receiver", "session", scope(), receipt(), "trace-1")
    retry = subject.receive("receiver", "session", scope(), receipt(accepted="99"), "trace-2")
    assert retry == first
    assert ledger.positions(scope())[0].quantity == Decimal("5")
    assert [event.event_type for event in subject.outbox] == ["ReceiptRecorded", "PutawayRequested"]


def test_reversal_preserves_receipt_evidence_and_creates_corrective_movement_once() -> None:
    subject, ledger, _ = service()
    original = subject.receive("receiver", "session", scope(), receipt(), "trace-1")
    reversed_outcome = subject.reverse("receiver", "session", scope(), "r-1", "receipt rejected", NOW, "trace-2")
    retry = subject.reverse("receiver", "session", scope(), "r-1", "ignored", NOW, "trace-3")
    assert reversed_outcome == retry
    assert reversed_outcome.receipt == original.receipt
    assert reversed_outcome.reversal_movement_id == "receipt-reversal-r-1"
    assert ledger.positions(scope())[0].quantity == Decimal("0")
    assert [event.event_type for event in subject.outbox] == ["ReceiptRecorded", "PutawayRequested", "ReceiptReversed"]


def test_tenant_isolation_and_unknown_tenant_reversal_are_rejected() -> None:
    subject, _, _ = service()
    subject.receive("receiver", "session", scope(), receipt(), "trace")
    with pytest.raises(ReceiptValidationError, match="unknown"):
        subject.reverse("receiver", "session", scope("tenant-b"), "r-1", "wrong tenant", NOW, "trace")
