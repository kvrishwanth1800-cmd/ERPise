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
from foundation.order import (
    Order,
    OrderAllocation,
    OrderCancellation,
    OrderFulfillment,
    OrderLine,
    OrderLineImmutableError,
    OrderManagementService,
    OrderRefund,
    OrderReturn,
    OrderStateError,
    OrderSubstitution,
    OrderValidationError,
)
from foundation.organization import ScopeContext

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
_ACTIONS = (
    "order.write",
    "order.allocate",
    "order.substitute",
    "order.cancel",
    "order.fulfill",
    "order.return.write",
    "order.refund",
)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id, is_tenant_administrator=True)


def service() -> OrderManagementService:
    access = AuthorizationService(SessionRevocationService())
    for action in _ACTIONS:
        access.grant(PermissionGrant("agent", "tenant-a", action))
    return OrderManagementService(access, AuditRecorder())


def order_line(line_id: str = "line-1") -> OrderLine:
    return OrderLine(line_id, "sku-1", Decimal("2"), Decimal("5.00"), Decimal("10.00"))


def order(order_id: str = "order-1", idempotency_key: str = "idem-1") -> Order:
    return Order(
        order_id,
        "tenant-a",
        "storefront",
        "customer-1",
        idempotency_key,
        (order_line(),),
        Decimal("10.00"),
        "USD",
        "reservation-1",
        "txn-1",
        NOW,
    )


def allocation(allocation_id: str = "alloc-1") -> OrderAllocation:
    return OrderAllocation(
        allocation_id, "tenant-a", "order-1", "line-1", "store-1", Decimal("2"), "move-1", NOW
    )


def order_return(return_id: str = "return-1", key: str = "idem-return-1") -> OrderReturn:
    return OrderReturn(
        return_id, "tenant-a", "order-1", key, (order_line(),), Decimal("10.00"), "damaged", NOW
    )


def placed(subject: OrderManagementService) -> Order:
    return subject.place_order("agent", "session", scope(), order(), "trace-place")


def fulfilled(subject: OrderManagementService) -> Order:
    placed(subject)
    subject.allocate("agent", "session", scope(), allocation(), "trace-allocate")
    return subject.fulfill(
        "agent",
        "session",
        scope(),
        OrderFulfillment("fulfil-1", "tenant-a", "order-1", "carrier-1", NOW),
        "trace-fulfill",
    )


def test_checkout_retry_with_the_same_idempotency_key_returns_one_order_outcome() -> None:
    subject = service()

    first = placed(subject)
    retry = subject.place_order("agent", "session", scope(), order(), "trace-retry")

    assert first == retry
    assert [event.event_type for event in subject.outbox] == ["OrderChanged"]
    assert len(subject.history(scope(), "order-1")) == 1


def test_order_commits_only_with_linked_reservation_and_payment_outcomes() -> None:
    subject = service()
    unlinked = Order(
        "order-2",
        "tenant-a",
        "storefront",
        "customer-1",
        "idem-2",
        (order_line(),),
        Decimal("10.00"),
        "USD",
        "",
        "",
        NOW,
    )

    with pytest.raises(OrderValidationError):
        subject.place_order("agent", "session", scope(), unlinked, "trace-place")

    assert subject.outbox == []


def test_order_totals_are_deterministic_over_its_lines() -> None:
    subject = service()
    mismatched = Order(
        "order-3",
        "tenant-a",
        "storefront",
        "customer-1",
        "idem-3",
        (order_line(),),
        Decimal("99.00"),
        "USD",
        "reservation-1",
        "txn-1",
        NOW,
    )

    with pytest.raises(OrderValidationError, match="total"):
        subject.place_order("agent", "session", scope(), mismatched, "trace-place")

    assert placed(subject).total_amount == Decimal("10.00")


def test_committed_order_lines_are_immutable() -> None:
    subject = service()
    placed(subject)

    with pytest.raises(OrderLineImmutableError):
        subject.edit_committed_lines("order-1")


def test_lifecycle_history_is_chronological_across_every_transition() -> None:
    subject = service()
    placed(subject)
    subject.allocate("agent", "session", scope(), allocation(), "trace-allocate")
    subject.substitute(
        "agent",
        "session",
        scope(),
        OrderSubstitution(
            "sub-1", "tenant-a", "order-1", "line-1", "sku-2", Decimal("1"), "out of stock", NOW
        ),
        "trace-substitute",
    )
    subject.fulfill(
        "agent",
        "session",
        scope(),
        OrderFulfillment("fulfil-1", "tenant-a", "order-1", "carrier-1", NOW),
        "trace-fulfill",
    )
    subject.record_return("agent", "session", scope(), order_return(), "trace-return")
    subject.refund(
        "agent",
        "session",
        scope(),
        OrderRefund(
            "refund-1",
            "tenant-a",
            "order-1",
            "return-1",
            "idem-refund-1",
            Decimal("10.00"),
            "payment-refund-1",
            NOW,
        ),
        "trace-refund",
    )

    history = subject.history(scope(), "order-1")
    assert [entry.transition for entry in history] == [
        "placed",
        "allocated",
        "substituted",
        "fulfilled",
        "returned",
        "refunded",
    ]
    assert [entry.sequence for entry in history] == [1, 2, 3, 4, 5, 6]
    assert subject.order(scope(), "order-1").status == "refunded"


def test_cancellation_is_recorded_and_closes_the_order_lifecycle() -> None:
    subject = service()
    placed(subject)

    cancelled = subject.cancel(
        "agent",
        "session",
        scope(),
        OrderCancellation("cancel-1", "tenant-a", "order-1", "customer request", NOW),
        "trace-cancel",
    )

    assert cancelled.status == "cancelled"
    assert [entry.transition for entry in subject.history(scope(), "order-1")] == [
        "placed",
        "cancelled",
    ]
    with pytest.raises(OrderStateError):
        subject.allocate("agent", "session", scope(), allocation("alloc-2"), "trace-allocate")


def test_invalid_transition_is_rejected_without_partial_lifecycle_effects() -> None:
    subject = service()
    placed(subject)
    events_before = tuple(subject.outbox)

    with pytest.raises(OrderStateError):
        subject.fulfill(
            "agent",
            "session",
            scope(),
            OrderFulfillment("fulfil-1", "tenant-a", "order-1", "carrier-1", NOW),
            "trace-fulfill",
        )

    assert subject.order(scope(), "order-1").status == "placed"
    assert tuple(subject.outbox) == events_before
    assert [entry.transition for entry in subject.history(scope(), "order-1")] == ["placed"]


def test_fulfillment_requires_an_allocation_for_every_order_line() -> None:
    subject = service()
    placed(subject)
    subject.allocate(
        "agent",
        "session",
        scope(),
        OrderAllocation(
            "alloc-1", "tenant-a", "order-1", "line-1", "store-1", Decimal("2"), "move-1", NOW
        ),
        "trace-allocate",
    )
    two_line_order = Order(
        "order-4",
        "tenant-a",
        "storefront",
        "customer-1",
        "idem-4",
        (order_line(), order_line("line-2")),
        Decimal("20.00"),
        "USD",
        "reservation-2",
        "txn-2",
        NOW,
    )
    subject.place_order("agent", "session", scope(), two_line_order, "trace-place")
    subject.allocate(
        "agent",
        "session",
        scope(),
        OrderAllocation(
            "alloc-4", "tenant-a", "order-4", "line-1", "store-1", Decimal("2"), "move-4", NOW
        ),
        "trace-allocate",
    )

    with pytest.raises(OrderStateError, match="allocation"):
        subject.fulfill(
            "agent",
            "session",
            scope(),
            OrderFulfillment("fulfil-4", "tenant-a", "order-4", "carrier-4", NOW),
            "trace-fulfill",
        )

    assert subject.order(scope(), "order-4").status == "allocated"


def test_allocation_and_substitution_cannot_exceed_the_ordered_line() -> None:
    subject = service()
    placed(subject)

    with pytest.raises(OrderValidationError):
        subject.allocate(
            "agent",
            "session",
            scope(),
            OrderAllocation(
                "alloc-9", "tenant-a", "order-1", "line-1", "store-1", Decimal("5"), "move-9", NOW
            ),
            "trace-allocate",
        )
    with pytest.raises(OrderValidationError):
        subject.substitute(
            "agent",
            "session",
            scope(),
            OrderSubstitution(
                "sub-9", "tenant-a", "order-1", "line-1", "sku-2", Decimal("5"), "swap", NOW
            ),
            "trace-substitute",
        )

    assert subject.allocations_for_order(scope(), "order-1") == ()


def test_duplicate_return_and_refund_keys_resolve_to_one_outcome() -> None:
    subject = service()
    fulfilled(subject)

    first_return = subject.record_return(
        "agent", "session", scope(), order_return(), "trace-return"
    )
    retried_return = subject.record_return(
        "agent", "session", scope(), order_return("return-2"), "trace-return-retry"
    )
    refund = OrderRefund(
        "refund-1",
        "tenant-a",
        "order-1",
        "return-1",
        "idem-refund-1",
        Decimal("10.00"),
        "payment-refund-1",
        NOW,
    )
    first_refund = subject.refund("agent", "session", scope(), refund, "trace-refund")
    retried_refund = subject.refund("agent", "session", scope(), refund, "trace-refund-retry")

    assert first_return == retried_return
    assert first_refund == retried_refund
    assert [entry.transition for entry in subject.history(scope(), "order-1")] == [
        "placed",
        "allocated",
        "fulfilled",
        "returned",
        "refunded",
    ]


def test_refund_requires_a_recorded_return_and_a_matching_amount() -> None:
    subject = service()
    fulfilled(subject)
    subject.record_return("agent", "session", scope(), order_return(), "trace-return")

    with pytest.raises(OrderValidationError, match="amount"):
        subject.refund(
            "agent",
            "session",
            scope(),
            OrderRefund(
                "refund-2",
                "tenant-a",
                "order-1",
                "return-1",
                "idem-refund-2",
                Decimal("25.00"),
                "payment-refund-2",
                NOW,
            ),
            "trace-refund",
        )

    assert subject.order(scope(), "order-1").status == "returned"


def test_orders_are_isolated_between_tenants() -> None:
    subject = service()
    placed(subject)

    with pytest.raises(OrderValidationError):
        subject.order(scope("tenant-b"), "order-1")


def test_unauthorized_order_command_is_denied() -> None:
    subject = service()

    with pytest.raises(AuthorizationDeniedError):
        subject.place_order("other", "session", scope(), order(), "trace-place")

    with pytest.raises(OrderValidationError):
        subject.order(scope(), "order-1")
