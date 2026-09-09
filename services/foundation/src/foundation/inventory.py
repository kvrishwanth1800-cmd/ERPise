# ruff: noqa: E501
"""Append-only inventory movements and deterministic position projection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class InventoryValidationError(ValueError):
    """Raised when an inventory movement cannot be posted."""


class PostedMovementImmutableError(InventoryValidationError):
    """Raised when an operation attempts to alter posted inventory truth."""


@dataclass(frozen=True)
class InventoryMovement:
    movement_id: str
    product_id: str
    location_id: str
    quantity_type: str
    quantity_delta: Decimal
    reason: str
    occurred_at: datetime
    reversal_of_movement_id: str | None = None


@dataclass(frozen=True)
class InventoryPosition:
    product_id: str
    location_id: str
    quantity_type: str
    quantity: Decimal


@dataclass(frozen=True)
class InventoryEvent:
    event_type: str
    tenant_id: str
    movement_id: str


class InventoryLedger:
    """Stores immutable movement facts and projects positions from those facts."""

    _quantity_types = frozenset({"physical", "reserved", "available", "quarantined", "damaged", "expired", "in_transit", "incoming"})

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._movements: dict[tuple[str, str], InventoryMovement] = {}
        self.outbox: list[InventoryEvent] = []

    def post(self, principal_id: str, session_id: str, scope: ScopeContext, movement: InventoryMovement, trace_id: str) -> InventoryMovement:
        self._authorization.authorize(principal_id, session_id, scope, "inventory.write")
        self._validate(movement)
        key = (scope.tenant_id, movement.movement_id)
        if key in self._movements:
            raise InventoryValidationError("movement id is already posted")
        if movement.reversal_of_movement_id is not None:
            original = self._movements.get((scope.tenant_id, movement.reversal_of_movement_id))
            if original is None:
                raise InventoryValidationError("reversal references an unknown movement")
            if (original.product_id, original.location_id, original.quantity_type) != (movement.product_id, movement.location_id, movement.quantity_type):
                raise InventoryValidationError("reversal scope must match the original movement")
            if movement.quantity_delta != -original.quantity_delta:
                raise InventoryValidationError("reversal quantity must exactly offset the original movement")
        self._movements[key] = movement
        self._audit.record(principal_id, "inventory.write", "inventory.post", movement.reason, "v1", trace_id, "allowed")
        self.outbox.append(InventoryEvent("InventoryMoved", scope.tenant_id, movement.movement_id))
        return movement

    def reverse(self, principal_id: str, session_id: str, scope: ScopeContext, movement_id: str, reversal_id: str, reason: str, occurred_at: datetime, trace_id: str) -> InventoryMovement:
        original = self._movements.get((scope.tenant_id, movement_id))
        if original is None:
            raise InventoryValidationError("movement to reverse is unknown")
        return self.post(principal_id, session_id, scope, InventoryMovement(reversal_id, original.product_id, original.location_id, original.quantity_type, -original.quantity_delta, reason, occurred_at, movement_id), trace_id)

    def positions(self, scope: ScopeContext) -> tuple[InventoryPosition, ...]:
        totals: defaultdict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
        for (tenant_id, _), movement in self._movements.items():
            if tenant_id == scope.tenant_id:
                totals[(movement.product_id, movement.location_id, movement.quantity_type)] += movement.quantity_delta
        return tuple(InventoryPosition(product_id, location_id, quantity_type, quantity) for (product_id, location_id, quantity_type), quantity in sorted(totals.items()))

    def replay_positions(self, scope: ScopeContext) -> tuple[InventoryPosition, ...]:
        result = self.positions(scope)
        self.outbox.append(InventoryEvent("InventoryPositionRebuilt", scope.tenant_id, "replay"))
        return result

    def edit_posted_movement(self, *_: object) -> None:
        raise PostedMovementImmutableError("posted inventory movements are immutable; post a reversal or compensation movement")

    @classmethod
    def _validate(cls, movement: InventoryMovement) -> None:
        if not all((movement.movement_id, movement.product_id, movement.location_id, movement.reason)):
            raise InventoryValidationError("movement identifiers and reason are required")
        if movement.quantity_type not in cls._quantity_types:
            raise InventoryValidationError("quantity type is invalid")
        if movement.quantity_delta == 0:
            raise InventoryValidationError("movement quantity cannot be zero")
        if movement.occurred_at.tzinfo is None:
            raise InventoryValidationError("movement timestamp must be timezone-aware")
