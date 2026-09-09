"""Tenant-scoped pickup and delivery fulfillment lifecycle services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext, ScopeDeniedError


class FulfillmentStateError(ValueError):
    """Raised when a fulfillment command cannot make a valid transition."""


@dataclass(frozen=True)
class FulfillmentPromise:
    fulfillment_id: str
    tenant_id: str
    order_id: str
    payment_id: str
    method: str
    slot_id: str
    address: str | None
    status: str = "promised"
    driver_id: str | None = None
    proof: str | None = None
    follow_up: str | None = None


@dataclass(frozen=True)
class FulfillmentChanged:
    fulfillment_id: str
    tenant_id: str
    status: str
    trace_id: str


class ReservationPort:
    """Tracks reservation release requests at the fulfillment boundary."""

    def __init__(self) -> None:
        self.released_order_ids: list[str] = []

    def release(self, order_id: str) -> None:
        self.released_order_ids.append(order_id)


class RefundPort:
    """Tracks payment refund requests at the fulfillment boundary."""

    def __init__(self) -> None:
        self.refunded_payment_ids: list[str] = []

    def refund(self, payment_id: str) -> None:
        self.refunded_payment_ids.append(payment_id)


class FulfillmentService:
    """Offers capacity-backed promises and records dispatch, proof, and failure recovery."""

    _methods = frozenset({"pickup", "delivery"})

    def __init__(self, audit: AuditRecorder, reservations: ReservationPort, refunds: RefundPort) -> None:
        self._audit = audit
        self._reservations = reservations
        self._refunds = refunds
        self._slot_capacity: dict[tuple[str, str], int] = {}
        self._serviceable_addresses: set[tuple[str, str]] = set()
        self._fulfillments: dict[str, FulfillmentPromise] = {}
        self._idempotent_results: dict[tuple[str, str], FulfillmentPromise] = {}
        self.outbox: list[FulfillmentChanged] = []

    def configure_slot(self, scope: ScopeContext, slot_id: str, capacity: int) -> None:
        self._require_tenant_admin(scope)
        if not slot_id or capacity < 0:
            raise FulfillmentStateError("Slots require an identifier and non-negative capacity.")
        self._slot_capacity[(scope.tenant_id, slot_id)] = capacity

    def allow_address(self, scope: ScopeContext, address: str) -> None:
        self._require_tenant_admin(scope)
        if not address:
            raise FulfillmentStateError("Service-area addresses must be non-empty.")
        self._serviceable_addresses.add((scope.tenant_id, address))

    def promise(
        self,
        scope: ScopeContext,
        fulfillment_id: str,
        order_id: str,
        payment_id: str,
        method: str,
        slot_id: str,
        address: str | None,
        idempotency_key: str,
        actor_id: str,
        trace_id: str,
    ) -> FulfillmentPromise:
        self._require_scope(scope, actor_id)
        key = (scope.tenant_id, idempotency_key)
        if key in self._idempotent_results:
            return self._idempotent_results[key]
        if not fulfillment_id or fulfillment_id in self._fulfillments or not order_id or not payment_id:
            raise FulfillmentStateError("Fulfillment, order, and payment identifiers must be unique and non-empty.")
        if method not in self._methods or not slot_id or not idempotency_key or not trace_id:
            raise FulfillmentStateError("Promise requires method, slot, idempotency key, and trace context.")
        if method == "delivery" and (not address or (scope.tenant_id, address) not in self._serviceable_addresses):
            raise FulfillmentStateError("Delivery address is outside the service area.")
        capacity_key = (scope.tenant_id, slot_id)
        if self._slot_capacity.get(capacity_key, 0) <= 0:
            raise FulfillmentStateError("The fulfillment slot has no remaining capacity.")
        self._slot_capacity[capacity_key] -= 1
        promise = FulfillmentPromise(fulfillment_id, scope.tenant_id, order_id, payment_id, method, slot_id, address)
        self._fulfillments[fulfillment_id] = promise
        self._idempotent_results[key] = promise
        self._record(promise, actor_id, trace_id, "promised")
        return promise

    def mark_ready(self, scope: ScopeContext, fulfillment_id: str, actor_id: str, trace_id: str) -> FulfillmentPromise:
        promise = self._get(scope, fulfillment_id)
        if promise.method != "pickup" or promise.status != "promised":
            raise FulfillmentStateError("Only promised pickup fulfillments can become ready.")
        return self._transition(promise, "ready", actor_id, trace_id)

    def collect(self, scope: ScopeContext, fulfillment_id: str, actor_id: str, trace_id: str) -> FulfillmentPromise:
        promise = self._get(scope, fulfillment_id)
        if promise.method != "pickup" or promise.status != "ready":
            raise FulfillmentStateError("Only ready pickup fulfillments can be collected.")
        return self._transition(promise, "collected", actor_id, trace_id)

    def assign(self, scope: ScopeContext, fulfillment_id: str, driver_id: str, actor_id: str, trace_id: str) -> FulfillmentPromise:
        promise = self._get(scope, fulfillment_id)
        if promise.method != "delivery" or promise.status != "promised" or not driver_id:
            raise FulfillmentStateError("Only promised deliveries can be assigned to a driver.")
        return self._transition(promise, "assigned", actor_id, trace_id, driver_id=driver_id)

    def dispatch(self, scope: ScopeContext, fulfillment_id: str, actor_id: str, trace_id: str) -> FulfillmentPromise:
        promise = self._get(scope, fulfillment_id)
        if promise.status != "assigned":
            raise FulfillmentStateError("Only assigned deliveries can be dispatched.")
        return self._transition(promise, "dispatched", actor_id, trace_id)

    def complete(self, scope: ScopeContext, fulfillment_id: str, proof: str, actor_id: str, trace_id: str) -> FulfillmentPromise:
        promise = self._get(scope, fulfillment_id)
        if promise.status not in {"dispatched", "ready"} or not proof:
            raise FulfillmentStateError("Completion requires an active fulfillment and proof.")
        return self._transition(promise, "completed", actor_id, trace_id, proof=proof)

    def fail(self, scope: ScopeContext, fulfillment_id: str, outcome: str, actor_id: str, trace_id: str) -> FulfillmentPromise:
        promise = self._get(scope, fulfillment_id)
        if promise.status not in {"promised", "assigned", "dispatched"} or outcome not in {"retry", "cancel_refund"}:
            raise FulfillmentStateError("Failure requires an active fulfillment and supported follow-up.")
        if outcome == "cancel_refund":
            self._reservations.release(promise.order_id)
            self._refunds.refund(promise.payment_id)
        return self._transition(promise, "failed", actor_id, trace_id, follow_up=outcome)

    def _get(self, scope: ScopeContext, fulfillment_id: str) -> FulfillmentPromise:
        promise = self._fulfillments.get(fulfillment_id)
        if promise is None or promise.tenant_id != scope.tenant_id:
            raise ScopeDeniedError("The fulfillment is outside the authorized tenant scope.")
        return promise

    def _transition(self, promise: FulfillmentPromise, status: str, actor_id: str, trace_id: str, **changes: str) -> FulfillmentPromise:
        updated = FulfillmentPromise(
            promise.fulfillment_id, promise.tenant_id, promise.order_id, promise.payment_id,
            promise.method, promise.slot_id, promise.address, status,
            changes.get("driver_id", promise.driver_id), changes.get("proof", promise.proof),
            changes.get("follow_up", promise.follow_up),
        )
        self._fulfillments[promise.fulfillment_id] = updated
        self._record(updated, actor_id, trace_id, status)
        return updated

    def _record(self, promise: FulfillmentPromise, actor_id: str, trace_id: str, result: str) -> None:
        if not actor_id or not trace_id:
            raise FulfillmentStateError("Fulfillment actions require actor and trace context.")
        self._audit.record(actor_id, "fulfillment", "fulfillment", "customer commitment", "fulfillment", trace_id, result)
        self.outbox.append(FulfillmentChanged(promise.fulfillment_id, promise.tenant_id, result, trace_id))

    @staticmethod
    def _require_tenant_admin(scope: ScopeContext) -> None:
        if not scope.is_tenant_administrator:
            raise ScopeDeniedError("Tenant administration is required to configure fulfillment.")

    @staticmethod
    def _require_scope(scope: ScopeContext, actor_id: str) -> None:
        if not scope.tenant_id or not actor_id:
            raise ScopeDeniedError("Tenant scope and actor identity are required.")
