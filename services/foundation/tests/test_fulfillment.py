import pytest

import foundation.audit as audit_module
import foundation.fulfillment as fulfillment_module
import foundation.organization as organization_module


@pytest.fixture
def scope() -> organization_module.ScopeContext:
    return organization_module.ScopeContext("tenant-a", is_tenant_administrator=True)


@pytest.fixture
def service() -> tuple[
    fulfillment_module.FulfillmentService,
    fulfillment_module.ReservationPort,
    fulfillment_module.RefundPort,
]:
    reservations = fulfillment_module.ReservationPort()
    refunds = fulfillment_module.RefundPort()
    return fulfillment_module.FulfillmentService(audit_module.AuditRecorder(), reservations, refunds), reservations, refunds


def configure(
    service: fulfillment_module.FulfillmentService,
    scope: organization_module.ScopeContext,
) -> None:
    service.configure_slot(scope, "slot-a", 2)
    service.allow_address(scope, "1 Main Street")


def test_delivery_lifecycle_records_assignment_dispatch_and_proof(
    scope: organization_module.ScopeContext,
    service: tuple[
        fulfillment_module.FulfillmentService,
        fulfillment_module.ReservationPort,
        fulfillment_module.RefundPort,
    ],
) -> None:
    fulfillment, _, _ = service
    configure(fulfillment, scope)
    promised = fulfillment.promise(
        scope, "fulfillment-a", "order-a", "payment-a", "delivery", "slot-a",
        "1 Main Street", "key-a", "shopper-a", "trace-a",
    )
    assigned = fulfillment.assign(
        scope, promised.fulfillment_id, "driver-a", "dispatcher-a", "trace-b"
    )
    dispatched = fulfillment.dispatch(scope, assigned.fulfillment_id, "driver-a", "trace-c")
    completed = fulfillment.complete(
        scope, dispatched.fulfillment_id, "signature-a", "driver-a", "trace-d"
    )
    assert completed.status == "completed"
    assert completed.proof == "signature-a"
    assert [event.status for event in fulfillment.outbox] == [
        "promised", "assigned", "dispatched", "completed"
    ]


def test_promise_is_capacity_backed_idempotent_and_tenant_scoped(
    scope: organization_module.ScopeContext,
    service: tuple[
        fulfillment_module.FulfillmentService,
        fulfillment_module.ReservationPort,
        fulfillment_module.RefundPort,
    ],
) -> None:
    fulfillment, _, _ = service
    configure(fulfillment, scope)
    first = fulfillment.promise(
        scope, "fulfillment-a", "order-a", "payment-a", "pickup", "slot-a",
        None, "key-a", "shopper-a", "trace-a",
    )
    assert fulfillment.promise(
        scope, "ignored", "order-a", "payment-a", "pickup", "slot-a",
        None, "key-a", "shopper-a", "trace-a",
    ) == first
    with pytest.raises(organization_module.ScopeDeniedError):
        fulfillment.collect(
            organization_module.ScopeContext("tenant-b", is_tenant_administrator=True),
            first.fulfillment_id,
            "shopper-b",
            "trace-b",
        )


def test_delivery_rejects_unserviceable_address_and_failed_delivery_releases_and_refunds(
    scope: organization_module.ScopeContext,
    service: tuple[
        fulfillment_module.FulfillmentService,
        fulfillment_module.ReservationPort,
        fulfillment_module.RefundPort,
    ],
) -> None:
    fulfillment, reservations, refunds = service
    fulfillment.configure_slot(scope, "slot-a", 1)
    with pytest.raises(fulfillment_module.FulfillmentStateError, match="outside the service area"):
        fulfillment.promise(
            scope, "bad", "order-a", "payment-a", "delivery", "slot-a",
            "unknown", "key-a", "shopper-a", "trace-a",
        )
    fulfillment.allow_address(scope, "1 Main Street")
    promised = fulfillment.promise(
        scope, "fulfillment-a", "order-a", "payment-a", "delivery", "slot-a",
        "1 Main Street", "key-b", "shopper-a", "trace-a",
    )
    failed = fulfillment.fail(
        scope, promised.fulfillment_id, "cancel_refund", "driver-a", "trace-b"
    )
    assert failed.follow_up == "cancel_refund"
    assert reservations.released_order_ids == ["order-a"]
    assert refunds.refunded_payment_ids == ["payment-a"]
