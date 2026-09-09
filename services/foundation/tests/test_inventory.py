# ruff: noqa: E501, I001
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from foundation.access import AuthorizationDeniedError, AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger, InventoryMovement, InventoryValidationError, PostedMovementImmutableError
from foundation.organization import ScopeContext

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def ledger() -> InventoryLedger:
    access = AuthorizationService(SessionRevocationService())
    access.grant(PermissionGrant("clerk", "tenant-a", "inventory.write"))
    return InventoryLedger(access, AuditRecorder())


def movement(movement_id: str, amount: str, reason: str = "receipt") -> InventoryMovement:
    return InventoryMovement(movement_id, "tea", "store-1", "physical", Decimal(amount), reason, NOW)


def test_authorized_movement_is_reasoned_and_projected() -> None:
    subject = ledger()
    subject.post("clerk", "session", scope(), movement("receive-1", "5"), "trace")
    assert subject.positions(scope())[0].quantity == Decimal("5")
    assert [event.event_type for event in subject.outbox] == ["InventoryMoved"]


def test_posted_movement_cannot_be_edited_and_duplicates_are_rejected() -> None:
    subject = ledger()
    subject.post("clerk", "session", scope(), movement("receive-1", "5"), "trace")
    with pytest.raises(PostedMovementImmutableError):
        subject.edit_posted_movement("receive-1")
    with pytest.raises(InventoryValidationError, match="already posted"):
        subject.post("clerk", "session", scope(), movement("receive-1", "7"), "trace-2")


def test_reversal_is_linked_and_exactly_offsets_original() -> None:
    subject = ledger()
    subject.post("clerk", "session", scope(), movement("receive-1", "5"), "trace")
    subject.reverse("clerk", "session", scope(), "receive-1", "reverse-1", "count correction", NOW, "trace-2")
    assert subject.positions(scope())[0].quantity == Decimal("0")
    with pytest.raises(InventoryValidationError, match="exactly offset"):
        subject.post("clerk", "session", scope(), InventoryMovement("bad-reverse", "tea", "store-1", "physical", Decimal("2"), "bad", NOW, "receive-1"), "trace-3")


def test_replay_reconciles_positions_and_tenant_scope_isolated() -> None:
    subject = ledger()
    subject.post("clerk", "session", scope(), movement("receive-1", "5"), "trace")
    subject.post("clerk", "session", scope(), movement("ship-1", "-2", "shipment"), "trace-2")
    assert subject.replay_positions(scope()) == subject.positions(scope())
    assert subject.replay_positions(scope())[0].quantity == Decimal("3")
    assert subject.positions(scope("tenant-b")) == ()
    assert subject.outbox[-1].event_type == "InventoryPositionRebuilt"


def test_unauthorized_operation_is_denied() -> None:
    subject = ledger()
    with pytest.raises(AuthorizationDeniedError):
        subject.post("other", "session", scope(), movement("receive-1", "5"), "trace")
