# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext
from foundation.pos import (
    PosOperationsService,
    RegisterShift,
    Return,
    ReturnAuthorityError,
    ReturnAuthorization,
    Sale,
    SaleLine,
    ShiftStateError,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id, is_tenant_administrator=True)


def service() -> PosOperationsService:
    access = AuthorizationService(SessionRevocationService())
    access.grant(PermissionGrant("clerk", "tenant-a", "pos.shift.write"))
    access.grant(PermissionGrant("clerk", "tenant-a", "pos.sale.write"))
    access.grant(PermissionGrant("supervisor", "tenant-a", "pos.return.authorize"))
    access.grant(PermissionGrant("clerk", "tenant-a", "pos.return.write"))
    return PosOperationsService(access, AuditRecorder())


def shift(shift_id: str = "shift-1", opening_float: str = "100.00") -> RegisterShift:
    return RegisterShift(
        shift_id, "tenant-a", "store-1", "register-1", "clerk", Decimal(opening_float), NOW
    )


def sale_line() -> SaleLine:
    return SaleLine("sku-1", Decimal("2"), Decimal("5.00"), Decimal("10.00"))


def sale(sale_id: str = "sale-1", shift_id: str = "shift-1", tender_type: str = "cash") -> Sale:
    return Sale(
        sale_id,
        "tenant-a",
        shift_id,
        f"idem-{sale_id}",
        (sale_line(),),
        Decimal("10.00"),
        "USD",
        tender_type,
        f"txn-{sale_id}",
        (f"move-{sale_id}",),
        f"receipt-{sale_id}",
        "online",
        NOW,
    )


def test_sale_requires_an_open_shift_and_is_idempotent_by_key() -> None:
    subject = service()
    subject.open_shift("clerk", "session", scope(), shift(), "trace")

    first = subject.record_sale("clerk", "session", scope(), sale(), "trace-sale")
    retry = subject.record_sale("clerk", "session", scope(), sale(), "trace-retry")

    assert first == retry
    assert [event.event_type for event in subject.outbox] == ["ShiftOpened", "SaleCompleted"]


def test_sale_is_rejected_when_the_shift_is_closed() -> None:
    subject = service()
    subject.open_shift("clerk", "session", scope(), shift(), "trace")
    subject.close_shift(
        "clerk", "session", scope(), "shift-1", Decimal("100.00"), "clerk", NOW, "trace-close"
    )

    with pytest.raises(ShiftStateError):
        subject.record_sale("clerk", "session", scope(), sale(), "trace-sale")


def test_return_requires_a_prior_granted_authorization() -> None:
    subject = service()
    subject.open_shift("clerk", "session", scope(), shift(), "trace")
    subject.record_sale("clerk", "session", scope(), sale(), "trace-sale")

    unauthorized_return = Return(
        "return-1",
        "tenant-a",
        "sale-1",
        "idem-return-1",
        (sale_line(),),
        Decimal("10.00"),
        "cash",
        "refund-1",
        ("move-return-1",),
        "customer request",
        NOW,
    )
    with pytest.raises(ReturnAuthorityError):
        subject.record_return("clerk", "session", scope(), unauthorized_return, "trace-return")

    subject.authorize_return(
        "supervisor",
        "session",
        scope(),
        ReturnAuthorization("auth-1", "tenant-a", "sale-1", "supervisor", NOW),
        "trace-authorize",
    )
    recorded = subject.record_return("clerk", "session", scope(), unauthorized_return, "trace-return")
    assert recorded.return_id == "return-1"


def test_shift_close_reconciles_declared_against_recorded_cash_activity() -> None:
    subject = service()
    subject.open_shift("clerk", "session", scope(), shift(), "trace")
    subject.record_sale("clerk", "session", scope(), sale(), "trace-sale")

    closed = subject.close_shift(
        "clerk", "session", scope(), "shift-1", Decimal("105.00"), "clerk", NOW, "trace-close"
    )

    assert closed.recorded_cash == Decimal("110.00")
    assert closed.variance == Decimal("-5.00")
    assert [event.event_type for event in subject.outbox][-2:] == [
        "ShiftClosed",
        "ShiftVarianceReported",
    ]


def test_offline_replay_of_a_queued_batch_is_safe_and_deduplicates() -> None:
    subject = service()
    subject.open_shift("clerk", "session", scope(), shift(), "trace")
    queued = sale(tender_type="cash")
    offline_sale = Sale(
        queued.sale_id,
        queued.tenant_id,
        queued.shift_id,
        queued.idempotency_key,
        queued.lines,
        queued.total_amount,
        queued.currency,
        queued.tender_type,
        queued.payment_transaction_id,
        queued.inventory_movement_ids,
        queued.receipt_id,
        "offline",
        queued.sold_at,
    )

    replayed = subject.replay_offline_sales(
        "clerk", "session", scope(), (offline_sale, offline_sale), "trace-replay"
    )

    assert replayed[0] == replayed[1]
    assert len([event for event in subject.outbox if event.event_type == "SaleCompleted"]) == 1


def test_unauthorized_operation_is_denied() -> None:
    subject = service()
    with pytest.raises(AuthorizationDeniedError):
        subject.open_shift("other", "session", scope(), shift(), "trace")
