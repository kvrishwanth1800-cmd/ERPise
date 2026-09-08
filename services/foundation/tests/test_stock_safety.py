# ruff: noqa: E501, I001
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from foundation.access import AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger, InventoryMovement
from foundation.organization import ScopeContext
from foundation.stock_safety import CountCommand, StockSafetyCommand, StockSafetyService, StockSafetyValidationError

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def service() -> tuple[StockSafetyService, InventoryLedger, AuditRecorder]:
    access = AuthorizationService(SessionRevocationService())
    for principal, action in (("counter", "stock_safety.count"), ("supervisor", "stock_safety.approve"), ("supervisor", "inventory.write"), ("operator", "stock_safety.write"), ("operator", "inventory.write"), ("seed", "inventory.write")):
        access.grant(PermissionGrant(principal, "tenant-a", action))
    audit = AuditRecorder()
    ledger = InventoryLedger(access, audit)
    ledger.post("seed", "session", scope(), InventoryMovement("seed-stock", "tea", "store-1", "physical", Decimal("10"), "opening stock", NOW), "seed")
    return StockSafetyService(access, audit, ledger), ledger, audit


def count(count_id: str = "count-1", observed: str = "8", threshold: str = "1") -> CountCommand:
    return CountCommand(count_id, "tea", "store-1", Decimal(observed), Decimal(threshold), "blind count", NOW)


def test_blind_count_withholds_expected_quantity_until_submission() -> None:
    subject, _, _ = service()
    started = subject.start_blind_count("counter", "session", scope(), count())
    assert started.count_id == "count-1"
    assert not hasattr(started, "expected_quantity")
    submitted = subject.submit_blind_count("counter", "session", scope(), "count-1", "trace")
    assert submitted.expected_quantity == Decimal("10")
    assert submitted.approval_required is True
    assert submitted.corrective_movement_id is None


def test_threshold_variance_requires_eligible_approval_before_correction() -> None:
    subject, ledger, audit = service()
    subject.start_blind_count("counter", "session", scope(), count())
    subject.submit_blind_count("counter", "session", scope(), "count-1", "trace")
    assert ledger.positions(scope())[0].quantity == Decimal("10")
    approved = subject.approve_variance("supervisor", "session", scope(), "count-1", "trace")
    assert approved.approved_by == "supervisor"
    assert approved.corrective_movement_id == "count-correction-count-1"
    assert ledger.positions(scope())[0].quantity == Decimal("8")
    assert [event.event_type for event in subject.outbox] == ["CountCompleted"]
    assert [record.source for record in audit.records].index("count.approved") < [record.source for record in audit.records].index("inventory.post", 1)


def test_expired_and_recalled_stock_are_not_sale_eligible_and_are_idempotent() -> None:
    subject, _, _ = service()
    expired = StockSafetyCommand("expiry-1", "tea", "store-1", "expired", Decimal("1"), "expiry date passed", "batch-7", NOW)
    recalled = StockSafetyCommand("recall-1", "coffee", "store-2", "recalled", Decimal("1"), "recall notice", "recall-22", NOW)
    assert subject.apply_safety_control("operator", "session", scope(), expired, "trace") == subject.apply_safety_control("operator", "session", scope(), expired, "retry")
    subject.apply_safety_control("operator", "session", scope(), recalled, "trace")
    assert not subject.is_sale_eligible(scope(), "tea", "store-1")
    assert not subject.is_sale_eligible(scope(), "coffee", "store-2")
    assert [event.event_type for event in subject.outbox] == ["StockSafetyChanged", "RecallActivated"]


def test_quarantine_and_disposal_retain_evidence_scope_and_corrective_effects() -> None:
    subject, ledger, audit = service()
    quarantine = StockSafetyCommand("q-1", "tea", "store-1", "quarantined", Decimal("3"), "quality hold", "inspection-7", NOW)
    quarantined = subject.apply_safety_control("operator", "session", scope(), quarantine, "trace")
    assert quarantined.corrective_movement_ids == ("safety-q-1-physical", "safety-q-1-quarantined")
    assert quarantine.evidence_id == "inspection-7"
    positions = {(item.quantity_type, item.quantity) for item in ledger.positions(scope())}
    assert ("physical", Decimal("7")) in positions
    assert ("quarantined", Decimal("3")) in positions
    disposed = subject.apply_safety_control("operator", "session", scope(), StockSafetyCommand("d-1", "tea", "store-1", "disposed", Decimal("2"), "destroyed", "disposal-8", NOW), "trace")
    assert disposed.corrective_movement_ids == ("safety-d-1-dispose",)
    assert [record.source for record in audit.records].index("stock.quarantined") < [record.source for record in audit.records].index("inventory.post", 1)


def test_tenant_isolation_rejects_another_tenants_count() -> None:
    subject, _, _ = service()
    subject.start_blind_count("counter", "session", scope(), count())
    with pytest.raises(StockSafetyValidationError, match="unknown"):
        subject.submit_blind_count("counter", "session", scope("tenant-b"), "count-1", "trace")
