"""Tenant-scoped order lifecycle: checkout, allocation, substitution, fulfillment, returns.

Order Management composes committed commercial outcomes (ADR-001: order commit follows
commercial confirmation): a placed order references the reservation and payment
transaction identifiers produced by the inventory and payment services, so one
committed checkout always resolves to exactly one linked stock, payment, and audit
outcome. Lifecycle facts are appended in chronological order (ADR-001: order history as
lifecycle evidence) and committed order lines never change in place. Every command
validates the full transition before it writes, so a rejected command leaves no partial
lifecycle effect.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class OrderValidationError(ValueError):
    """Raised when an order command cannot preserve the required order evidence."""


class OrderStateError(OrderValidationError):
    """Raised when a lifecycle transition is invalid for the order's current state."""


class OrderLineImmutableError(OrderValidationError):
    """Raised when an operation attempts to alter committed order lines."""


@dataclass(frozen=True)
class OrderLine:
    line_id: str
    product_id: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class Order:
    order_id: str
    tenant_id: str
    channel: str
    customer_id: str
    idempotency_key: str
    lines: tuple[OrderLine, ...]
    total_amount: Decimal
    currency: str
    reservation_id: str
    payment_transaction_id: str
    placed_at: datetime
    status: str = "placed"


@dataclass(frozen=True)
class OrderAllocation:
    allocation_id: str
    tenant_id: str
    order_id: str
    line_id: str
    location_id: str
    quantity: Decimal
    inventory_movement_id: str
    allocated_at: datetime


@dataclass(frozen=True)
class OrderSubstitution:
    substitution_id: str
    tenant_id: str
    order_id: str
    line_id: str
    substitute_product_id: str
    quantity: Decimal
    reason: str
    substituted_at: datetime


@dataclass(frozen=True)
class OrderFulfillment:
    fulfillment_id: str
    tenant_id: str
    order_id: str
    carrier_reference: str
    fulfilled_at: datetime


@dataclass(frozen=True)
class OrderCancellation:
    cancellation_id: str
    tenant_id: str
    order_id: str
    reason: str
    cancelled_at: datetime


@dataclass(frozen=True)
class OrderReturn:
    return_id: str
    tenant_id: str
    order_id: str
    idempotency_key: str
    lines: tuple[OrderLine, ...]
    total_amount: Decimal
    reason: str
    returned_at: datetime


@dataclass(frozen=True)
class OrderRefund:
    refund_id: str
    tenant_id: str
    order_id: str
    return_id: str
    idempotency_key: str
    amount: Decimal
    payment_refund_id: str
    refunded_at: datetime


@dataclass(frozen=True)
class OrderHistoryEntry:
    entry_id: str
    tenant_id: str
    order_id: str
    sequence: int
    transition: str
    from_status: str
    to_status: str
    detail: str
    occurred_at: datetime


@dataclass(frozen=True)
class OrderEvent:
    event_type: str
    tenant_id: str
    subject_id: str


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "placed": frozenset({"allocated", "cancelled"}),
    "allocated": frozenset({"allocated", "fulfilled", "cancelled"}),
    "fulfilled": frozenset({"returned"}),
    "returned": frozenset({"refunded"}),
    "cancelled": frozenset(),
    "refunded": frozenset(),
}


class OrderManagementService:
    """Runs idempotent checkout and the order lifecycle over committed commercial facts."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._orders: dict[tuple[str, str], Order] = {}
        self._orders_by_key: dict[tuple[str, str], Order] = {}
        self._allocations: dict[tuple[str, str], OrderAllocation] = {}
        self._substitutions: dict[tuple[str, str], OrderSubstitution] = {}
        self._fulfillments: dict[tuple[str, str], OrderFulfillment] = {}
        self._cancellations: dict[tuple[str, str], OrderCancellation] = {}
        self._returns: dict[tuple[str, str], OrderReturn] = {}
        self._returns_by_key: dict[tuple[str, str], OrderReturn] = {}
        self._refunds: dict[tuple[str, str], OrderRefund] = {}
        self._refunds_by_key: dict[tuple[str, str], OrderRefund] = {}
        self._history: dict[tuple[str, str], list[OrderHistoryEntry]] = {}
        self.outbox: list[OrderEvent] = []

    def place_order(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        order: Order,
        trace_id: str,
    ) -> Order:
        """Commits one checkout outcome; a retry with the same key returns that outcome."""
        self._authorize(principal_id, session_id, scope, "order.write")
        key = (scope.tenant_id, order.idempotency_key)
        existing = self._orders_by_key.get(key)
        if existing is not None:
            return existing
        self._validate_order(order, scope)
        if (scope.tenant_id, order.order_id) in self._orders:
            raise OrderValidationError("order identifiers are immutable")
        self._store(scope, order)
        self._append_history(
            scope, order.order_id, "placed", "new", "placed", order.channel, order.placed_at
        )
        self._record(principal_id, "order.place", order.order_id, trace_id)
        self.outbox.append(OrderEvent("OrderChanged", scope.tenant_id, order.order_id))
        return order

    def allocate(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        allocation: OrderAllocation,
        trace_id: str,
    ) -> OrderAllocation:
        self._authorize(principal_id, session_id, scope, "order.allocate")
        order = self.order(scope, allocation.order_id)
        self._require_transition(order, "allocated")
        line = self._line(order, allocation.line_id)
        if (
            not all(
                (
                    allocation.allocation_id,
                    allocation.location_id,
                    allocation.inventory_movement_id,
                )
            )
            or allocation.tenant_id != scope.tenant_id
            or allocation.quantity <= 0
            or allocation.quantity > line.quantity
            or allocation.allocated_at.tzinfo is None
        ):
            raise OrderValidationError(
                "allocations require identity, a committed stock movement reference, a "
                "quantity within the ordered line, and timezone-aware time"
            )
        key = (scope.tenant_id, allocation.allocation_id)
        if key in self._allocations:
            raise OrderValidationError("allocation identifiers are immutable")
        self._allocations[key] = allocation
        self._store(scope, replace(order, status="allocated"))
        self._append_history(
            scope,
            order.order_id,
            "allocated",
            order.status,
            "allocated",
            allocation.allocation_id,
            allocation.allocated_at,
        )
        self._record(principal_id, "order.allocate", allocation.allocation_id, trace_id)
        self.outbox.append(OrderEvent("FulfillmentChanged", scope.tenant_id, order.order_id))
        return allocation

    def substitute(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        substitution: OrderSubstitution,
        trace_id: str,
    ) -> OrderSubstitution:
        self._authorize(principal_id, session_id, scope, "order.substitute")
        order = self.order(scope, substitution.order_id)
        self._require_transition(order, "allocated")
        line = self._line(order, substitution.line_id)
        if (
            not all(
                (
                    substitution.substitution_id,
                    substitution.substitute_product_id,
                    substitution.reason,
                )
            )
            or substitution.tenant_id != scope.tenant_id
            or substitution.substitute_product_id == line.product_id
            or substitution.quantity <= 0
            or substitution.quantity > line.quantity
            or substitution.substituted_at.tzinfo is None
        ):
            raise OrderValidationError(
                "substitutions require identity, a different substitute product, a reason, "
                "a quantity within the ordered line, and timezone-aware time"
            )
        key = (scope.tenant_id, substitution.substitution_id)
        if key in self._substitutions:
            raise OrderValidationError("substitution identifiers are immutable")
        self._substitutions[key] = substitution
        self._append_history(
            scope,
            order.order_id,
            "substituted",
            order.status,
            order.status,
            substitution.substitution_id,
            substitution.substituted_at,
        )
        self._record(principal_id, "order.substitute", substitution.substitution_id, trace_id)
        self.outbox.append(OrderEvent("FulfillmentChanged", scope.tenant_id, order.order_id))
        return substitution

    def cancel(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        cancellation: OrderCancellation,
        trace_id: str,
    ) -> Order:
        self._authorize(principal_id, session_id, scope, "order.cancel")
        order = self.order(scope, cancellation.order_id)
        self._require_transition(order, "cancelled")
        if (
            not all((cancellation.cancellation_id, cancellation.reason))
            or cancellation.tenant_id != scope.tenant_id
            or cancellation.cancelled_at.tzinfo is None
        ):
            raise OrderValidationError(
                "cancellations require identity, a reason, and timezone-aware time"
            )
        key = (scope.tenant_id, cancellation.cancellation_id)
        if key in self._cancellations:
            raise OrderValidationError("cancellation identifiers are immutable")
        self._cancellations[key] = cancellation
        cancelled = replace(order, status="cancelled")
        self._store(scope, cancelled)
        self._append_history(
            scope,
            order.order_id,
            "cancelled",
            order.status,
            "cancelled",
            cancellation.reason,
            cancellation.cancelled_at,
        )
        self._record(principal_id, "order.cancel", order.order_id, trace_id)
        self.outbox.append(OrderEvent("OrderChanged", scope.tenant_id, order.order_id))
        return cancelled

    def fulfill(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        fulfillment: OrderFulfillment,
        trace_id: str,
    ) -> Order:
        self._authorize(principal_id, session_id, scope, "order.fulfill")
        order = self.order(scope, fulfillment.order_id)
        self._require_transition(order, "fulfilled")
        if (
            not all((fulfillment.fulfillment_id, fulfillment.carrier_reference))
            or fulfillment.tenant_id != scope.tenant_id
            or fulfillment.fulfilled_at.tzinfo is None
        ):
            raise OrderValidationError(
                "fulfillments require identity, a carrier reference, and timezone-aware time"
            )
        if self._allocated_line_ids(scope, order.order_id) != {
            line.line_id for line in order.lines
        }:
            raise OrderStateError("every order line requires an allocation before fulfillment")
        key = (scope.tenant_id, fulfillment.fulfillment_id)
        if key in self._fulfillments:
            raise OrderValidationError("fulfillment identifiers are immutable")
        self._fulfillments[key] = fulfillment
        fulfilled = replace(order, status="fulfilled")
        self._store(scope, fulfilled)
        self._append_history(
            scope,
            order.order_id,
            "fulfilled",
            order.status,
            "fulfilled",
            fulfillment.carrier_reference,
            fulfillment.fulfilled_at,
        )
        self._record(principal_id, "order.fulfill", fulfillment.fulfillment_id, trace_id)
        self.outbox.append(OrderEvent("FulfillmentChanged", scope.tenant_id, order.order_id))
        return fulfilled

    def record_return(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        return_: OrderReturn,
        trace_id: str,
    ) -> OrderReturn:
        self._authorize(principal_id, session_id, scope, "order.return.write")
        key = (scope.tenant_id, return_.idempotency_key)
        existing = self._returns_by_key.get(key)
        if existing is not None:
            return existing
        order = self.order(scope, return_.order_id)
        self._require_transition(order, "returned")
        if (
            not all((return_.return_id, return_.idempotency_key, return_.reason))
            or return_.tenant_id != scope.tenant_id
            or not return_.lines
            or return_.total_amount != sum((line.line_total for line in return_.lines), Decimal())
            or return_.returned_at.tzinfo is None
        ):
            raise OrderValidationError(
                "returns require identity, a reason, at least one line, a total matching the "
                "lines, and timezone-aware time"
            )
        for line in return_.lines:
            ordered = self._line(order, line.line_id)
            if line.product_id != ordered.product_id or line.quantity > ordered.quantity:
                raise OrderValidationError(
                    "returned lines must match the ordered product and quantity"
                )
        if (scope.tenant_id, return_.return_id) in self._returns:
            raise OrderValidationError("return identifiers are immutable")
        self._returns[(scope.tenant_id, return_.return_id)] = return_
        self._returns_by_key[key] = return_
        self._store(scope, replace(order, status="returned"))
        self._append_history(
            scope,
            order.order_id,
            "returned",
            order.status,
            "returned",
            return_.return_id,
            return_.returned_at,
        )
        self._record(principal_id, "order.return", return_.return_id, trace_id)
        self.outbox.append(OrderEvent("OrderChanged", scope.tenant_id, order.order_id))
        return return_

    def refund(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        refund: OrderRefund,
        trace_id: str,
    ) -> OrderRefund:
        self._authorize(principal_id, session_id, scope, "order.refund")
        key = (scope.tenant_id, refund.idempotency_key)
        existing = self._refunds_by_key.get(key)
        if existing is not None:
            return existing
        order = self.order(scope, refund.order_id)
        self._require_transition(order, "refunded")
        returned = self._returns.get((scope.tenant_id, refund.return_id))
        if returned is None or returned.order_id != order.order_id:
            raise OrderValidationError("refunds require a recorded return for this order")
        if (
            not all((refund.refund_id, refund.idempotency_key, refund.payment_refund_id))
            or refund.tenant_id != scope.tenant_id
            or refund.amount != returned.total_amount
            or refund.refunded_at.tzinfo is None
        ):
            raise OrderValidationError(
                "refunds require identity, a committed payment refund reference, an amount "
                "matching the return total, and timezone-aware time"
            )
        if (scope.tenant_id, refund.refund_id) in self._refunds:
            raise OrderValidationError("refund identifiers are immutable")
        self._refunds[(scope.tenant_id, refund.refund_id)] = refund
        self._refunds_by_key[key] = refund
        self._store(scope, replace(order, status="refunded"))
        self._append_history(
            scope,
            order.order_id,
            "refunded",
            order.status,
            "refunded",
            refund.payment_refund_id,
            refund.refunded_at,
        )
        self._record(principal_id, "order.refund", refund.refund_id, trace_id)
        self.outbox.append(OrderEvent("OrderChanged", scope.tenant_id, order.order_id))
        return refund

    def order(self, scope: ScopeContext, order_id: str) -> Order:
        order = self._orders.get((scope.tenant_id, order_id))
        if order is None:
            raise OrderValidationError("order is outside tenant scope or unknown")
        return order

    def history(self, scope: ScopeContext, order_id: str) -> tuple[OrderHistoryEntry, ...]:
        """Returns the order's lifecycle facts in chronological sequence order."""
        self.order(scope, order_id)
        entries = self._history.get((scope.tenant_id, order_id), [])
        return tuple(sorted(entries, key=lambda entry: entry.sequence))

    def allocations_for_order(
        self, scope: ScopeContext, order_id: str
    ) -> tuple[OrderAllocation, ...]:
        self.order(scope, order_id)
        return tuple(
            allocation
            for allocation in self._allocations.values()
            if allocation.tenant_id == scope.tenant_id and allocation.order_id == order_id
        )

    def edit_committed_lines(self, *_: object) -> None:
        raise OrderLineImmutableError(
            "committed order lines are immutable; record a substitution, cancellation, or "
            "return instead"
        )

    def _allocated_line_ids(self, scope: ScopeContext, order_id: str) -> set[str]:
        return {
            allocation.line_id for allocation in self.allocations_for_order(scope, order_id)
        }

    def _store(self, scope: ScopeContext, order: Order) -> None:
        self._orders[(scope.tenant_id, order.order_id)] = order
        self._orders_by_key[(scope.tenant_id, order.idempotency_key)] = order

    def _append_history(
        self,
        scope: ScopeContext,
        order_id: str,
        transition: str,
        from_status: str,
        to_status: str,
        detail: str,
        occurred_at: datetime,
    ) -> OrderHistoryEntry:
        entries = self._history.setdefault((scope.tenant_id, order_id), [])
        sequence = len(entries) + 1
        entry = OrderHistoryEntry(
            f"{order_id}-{sequence}",
            scope.tenant_id,
            order_id,
            sequence,
            transition,
            from_status,
            to_status,
            detail,
            occurred_at,
        )
        entries.append(entry)
        return entry

    @staticmethod
    def _require_transition(order: Order, to_status: str) -> None:
        if to_status not in _ALLOWED_TRANSITIONS[order.status]:
            raise OrderStateError(
                f"an order in the {order.status} state cannot move to {to_status}"
            )

    @staticmethod
    def _line(order: Order, line_id: str) -> OrderLine:
        for line in order.lines:
            if line.line_id == line_id:
                return line
        raise OrderValidationError("order line is unknown for this order")

    @staticmethod
    def _validate_order(order: Order, scope: ScopeContext) -> None:
        if (
            not all(
                (
                    order.order_id,
                    order.channel,
                    order.customer_id,
                    order.idempotency_key,
                    order.currency,
                )
            )
            or order.tenant_id != scope.tenant_id
            or order.status != "placed"
            or order.placed_at.tzinfo is None
        ):
            raise OrderValidationError(
                "orders require identity, a channel, a customer, a currency, the placed "
                "state, and timezone-aware time"
            )
        if not all((order.reservation_id, order.payment_transaction_id)):
            raise OrderValidationError(
                "orders commit only with a committed reservation and payment transaction "
                "reference"
            )
        if not order.lines:
            raise OrderValidationError("orders require at least one line")
        line_ids = {line.line_id for line in order.lines}
        if len(line_ids) != len(order.lines) or not all(line_ids):
            raise OrderValidationError("order line identifiers must be unique and non-empty")
        for line in order.lines:
            if (
                not line.product_id
                or line.quantity <= 0
                or line.unit_price < 0
                or line.line_total != line.quantity * line.unit_price
            ):
                raise OrderValidationError(
                    "order lines require a product, a positive quantity, a non-negative "
                    "unit price, and a line total equal to quantity times unit price"
                )
        if order.total_amount != sum((line.line_total for line in order.lines), Decimal()):
            raise OrderValidationError("order total must equal the sum of its line totals")

    def _authorize(
        self, principal_id: str, session_id: str, scope: ScopeContext, action: str
    ) -> None:
        self._authorization.authorize(principal_id, session_id, scope, action)

    def _record(self, actor_id: str, source: str, subject: str, trace_id: str) -> None:
        self._audit.record(actor_id, source, "order-service", subject, "v1", trace_id, "allowed")
