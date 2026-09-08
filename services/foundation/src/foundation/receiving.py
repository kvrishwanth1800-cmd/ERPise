# ruff: noqa: E501
"""Receipt evidence, discrepancy capture, putaway requests, and corrective stock effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger, InventoryMovement
from foundation.organization import ScopeContext


class ReceiptValidationError(ValueError):
    """Raised when receipt evidence cannot produce a traceable stock effect."""


@dataclass(frozen=True)
class ReceiptCommand:
    receipt_id: str
    product_id: str
    receiving_location_id: str
    putaway_location_id: str
    accepted_quantity: Decimal
    expected_quantity: Decimal | None
    unit_cost: Decimal | None
    batch_id: str | None
    serial_id: str | None
    expires_on: date | None
    received_at: datetime
    reason: str


@dataclass(frozen=True)
class ReceiptOutcome:
    receipt: ReceiptCommand
    movement_id: str
    discrepancy_quantity: Decimal | None
    putaway_task_id: str
    reversed: bool = False
    reversal_movement_id: str | None = None


@dataclass(frozen=True)
class ReceivingEvent:
    event_type: str
    tenant_id: str
    receipt_id: str


class ReceivingService:
    """Captures receipt evidence before delegating append-only facts to InventoryLedger."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder, ledger: InventoryLedger) -> None:
        self._authorization = authorization
        self._audit = audit
        self._ledger = ledger
        self._outcomes: dict[tuple[str, str], ReceiptOutcome] = {}
        self.outbox: list[ReceivingEvent] = []

    def receive(self, principal_id: str, session_id: str, scope: ScopeContext, command: ReceiptCommand, trace_id: str) -> ReceiptOutcome:
        self._authorization.authorize(principal_id, session_id, scope, "receiving.write")
        self._validate(command)
        key = (scope.tenant_id, command.receipt_id)
        if existing := self._outcomes.get(key):
            return existing
        discrepancy = None if command.expected_quantity is None else command.accepted_quantity - command.expected_quantity
        if discrepancy is not None:
            self._audit.record(principal_id, "receiving.write", "receipt.discrepancy", command.reason, "v1", trace_id, "recorded")
        movement_id = f"receipt-{command.receipt_id}"
        self._ledger.post(
            principal_id,
            session_id,
            scope,
            InventoryMovement(movement_id, command.product_id, command.receiving_location_id, "physical", command.accepted_quantity, command.reason, command.received_at),
            trace_id,
        )
        outcome = ReceiptOutcome(command, movement_id, discrepancy, f"putaway-{command.receipt_id}")
        self._outcomes[key] = outcome
        self._audit.record(principal_id, "receiving.write", "receipt.record", command.reason, "v1", trace_id, "recorded")
        self.outbox.extend((ReceivingEvent("ReceiptRecorded", scope.tenant_id, command.receipt_id), ReceivingEvent("PutawayRequested", scope.tenant_id, command.receipt_id)))
        return outcome

    def reverse(self, principal_id: str, session_id: str, scope: ScopeContext, receipt_id: str, reason: str, occurred_at: datetime, trace_id: str) -> ReceiptOutcome:
        self._authorization.authorize(principal_id, session_id, scope, "receiving.write")
        outcome = self._outcomes.get((scope.tenant_id, receipt_id))
        if outcome is None:
            raise ReceiptValidationError("receipt is unknown in this tenant")
        if outcome.reversed:
            return outcome
        reversal_id = f"receipt-reversal-{receipt_id}"
        self._ledger.reverse(principal_id, session_id, scope, outcome.movement_id, reversal_id, reason, occurred_at, trace_id)
        corrected = ReceiptOutcome(outcome.receipt, outcome.movement_id, outcome.discrepancy_quantity, outcome.putaway_task_id, True, reversal_id)
        self._outcomes[(scope.tenant_id, receipt_id)] = corrected
        self._audit.record(principal_id, "receiving.write", "receipt.reverse", reason, "v1", trace_id, "corrected")
        self.outbox.append(ReceivingEvent("ReceiptReversed", scope.tenant_id, receipt_id))
        return corrected

    @staticmethod
    def _validate(command: ReceiptCommand) -> None:
        if not all((command.receipt_id, command.product_id, command.receiving_location_id, command.putaway_location_id, command.reason)):
            raise ReceiptValidationError("receipt identifiers, locations, and reason are required")
        if command.accepted_quantity <= 0:
            raise ReceiptValidationError("accepted quantity must be positive")
        if command.expected_quantity is not None and command.expected_quantity < 0:
            raise ReceiptValidationError("expected quantity cannot be negative")
        if command.unit_cost is not None and command.unit_cost < 0:
            raise ReceiptValidationError("unit cost cannot be negative")
        if command.received_at.tzinfo is None:
            raise ReceiptValidationError("receipt timestamp must be timezone-aware")
