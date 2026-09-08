# ruff: noqa: E501
"""Blind counts, variance approval, and unsafe-stock controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger, InventoryMovement
from foundation.organization import ScopeContext


class StockSafetyValidationError(ValueError):
    """Raised when a count or safety command cannot create traceable stock facts."""


@dataclass(frozen=True)
class CountCommand:
    count_id: str
    product_id: str
    location_id: str
    observed_quantity: Decimal
    approval_threshold: Decimal
    reason: str
    counted_at: datetime


@dataclass(frozen=True)
class CountOutcome:
    command: CountCommand
    expected_quantity: Decimal
    variance_quantity: Decimal
    approval_required: bool
    approved_by: str | None = None
    corrective_movement_id: str | None = None


@dataclass(frozen=True)
class StockSafetyCommand:
    safety_id: str
    product_id: str
    location_id: str
    action: str
    quantity: Decimal
    reason: str
    evidence_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class StockSafetyOutcome:
    command: StockSafetyCommand
    corrective_movement_ids: tuple[str, ...]


@dataclass(frozen=True)
class StockSafetyEvent:
    event_type: str
    tenant_id: str
    subject_id: str


class StockSafetyService:
    """Captures safety evidence before requesting immutable inventory corrections."""

    _safety_actions = frozenset({"expired", "recalled", "quarantined", "disposed"})

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder, ledger: InventoryLedger) -> None:
        self._authorization = authorization
        self._audit = audit
        self._ledger = ledger
        self._counts: dict[tuple[str, str], CountOutcome] = {}
        self._safety: dict[tuple[str, str], StockSafetyOutcome] = {}
        self._ineligible: set[tuple[str, str, str]] = set()
        self.outbox: list[StockSafetyEvent] = []

    def start_blind_count(self, principal_id: str, session_id: str, scope: ScopeContext, command: CountCommand) -> CountOutcome:
        self._authorization.authorize(principal_id, session_id, scope, "stock_safety.count")
        self._validate_count(command)
        key = (scope.tenant_id, command.count_id)
        if existing := self._counts.get(key):
            return existing
        expected = self._physical_quantity(scope, command.product_id, command.location_id)
        outcome = CountOutcome(command, expected, command.observed_quantity - expected, False)
        self._counts[key] = outcome
        self._audit.record(principal_id, "stock_safety.count", "count.started", command.reason, "v1", command.count_id, "recorded")
        return outcome

    def submit_blind_count(self, principal_id: str, session_id: str, scope: ScopeContext, count_id: str, trace_id: str) -> CountOutcome:
        self._authorization.authorize(principal_id, session_id, scope, "stock_safety.count")
        outcome = self._get_count(scope, count_id)
        if principal_id != "" and outcome.approved_by is not None:
            return outcome
        threshold_exceeded = abs(outcome.variance_quantity) > outcome.command.approval_threshold
        submitted = CountOutcome(outcome.command, outcome.expected_quantity, outcome.variance_quantity, threshold_exceeded)
        self._counts[(scope.tenant_id, count_id)] = submitted
        self._audit.record(principal_id, "stock_safety.count", "count.completed", outcome.command.reason, "v1", trace_id, "pending_approval" if threshold_exceeded else "completed")
        if threshold_exceeded:
            return submitted
        return self._post_count_correction(principal_id, session_id, scope, submitted, trace_id)

    def approve_variance(self, principal_id: str, session_id: str, scope: ScopeContext, count_id: str, trace_id: str) -> CountOutcome:
        self._authorization.authorize(principal_id, session_id, scope, "stock_safety.approve")
        outcome = self._get_count(scope, count_id)
        if not outcome.approval_required:
            raise StockSafetyValidationError("count variance does not require approval")
        if outcome.approved_by is not None:
            return outcome
        if principal_id == "":
            raise StockSafetyValidationError("an eligible approver is required")
        approved = CountOutcome(outcome.command, outcome.expected_quantity, outcome.variance_quantity, True, principal_id)
        self._counts[(scope.tenant_id, count_id)] = approved
        self._audit.record(principal_id, "stock_safety.approve", "count.approved", outcome.command.reason, "v1", trace_id, "approved")
        return self._post_count_correction(principal_id, session_id, scope, approved, trace_id)

    def is_sale_eligible(self, scope: ScopeContext, product_id: str, location_id: str) -> bool:
        return (scope.tenant_id, product_id, location_id) not in self._ineligible

    def apply_safety_control(self, principal_id: str, session_id: str, scope: ScopeContext, command: StockSafetyCommand, trace_id: str) -> StockSafetyOutcome:
        self._authorization.authorize(principal_id, session_id, scope, "stock_safety.write")
        self._validate_safety(command)
        key = (scope.tenant_id, command.safety_id)
        if existing := self._safety.get(key):
            return existing
        self._audit.record(principal_id, "stock_safety.write", f"stock.{command.action}", command.reason, "v1", trace_id, "recorded")
        movement_ids: list[str] = []
        if command.action == "quarantined":
            physical_id = f"safety-{command.safety_id}-physical"
            quarantined_id = f"safety-{command.safety_id}-quarantined"
            self._ledger.post(principal_id, session_id, scope, InventoryMovement(physical_id, command.product_id, command.location_id, "physical", -command.quantity, command.reason, command.occurred_at), trace_id)
            self._ledger.post(principal_id, session_id, scope, InventoryMovement(quarantined_id, command.product_id, command.location_id, "quarantined", command.quantity, command.reason, command.occurred_at), trace_id)
            movement_ids.extend((physical_id, quarantined_id))
        elif command.action == "disposed":
            movement_id = f"safety-{command.safety_id}-dispose"
            self._ledger.post(principal_id, session_id, scope, InventoryMovement(movement_id, command.product_id, command.location_id, "physical", -command.quantity, command.reason, command.occurred_at), trace_id)
            movement_ids.append(movement_id)
        self._ineligible.add((scope.tenant_id, command.product_id, command.location_id))
        outcome = StockSafetyOutcome(command, tuple(movement_ids))
        self._safety[key] = outcome
        event_type = "RecallActivated" if command.action == "recalled" else "StockQuarantined" if command.action == "quarantined" else "StockSafetyChanged"
        self.outbox.append(StockSafetyEvent(event_type, scope.tenant_id, command.safety_id))
        return outcome

    def _post_count_correction(self, principal_id: str, session_id: str, scope: ScopeContext, outcome: CountOutcome, trace_id: str) -> CountOutcome:
        if outcome.corrective_movement_id is not None:
            return outcome
        movement_id = f"count-correction-{outcome.command.count_id}"
        if outcome.variance_quantity != 0:
            self._ledger.post(principal_id, session_id, scope, InventoryMovement(movement_id, outcome.command.product_id, outcome.command.location_id, "physical", outcome.variance_quantity, outcome.command.reason, outcome.command.counted_at), trace_id)
        completed = CountOutcome(outcome.command, outcome.expected_quantity, outcome.variance_quantity, outcome.approval_required, outcome.approved_by, movement_id)
        self._counts[(scope.tenant_id, outcome.command.count_id)] = completed
        self.outbox.append(StockSafetyEvent("CountCompleted", scope.tenant_id, outcome.command.count_id))
        return completed

    def _get_count(self, scope: ScopeContext, count_id: str) -> CountOutcome:
        outcome = self._counts.get((scope.tenant_id, count_id))
        if outcome is None:
            raise StockSafetyValidationError("count is unknown in this tenant")
        return outcome

    def _physical_quantity(self, scope: ScopeContext, product_id: str, location_id: str) -> Decimal:
        return sum((position.quantity for position in self._ledger.positions(scope) if (position.product_id, position.location_id, position.quantity_type) == (product_id, location_id, "physical")), Decimal())

    @staticmethod
    def _validate_count(command: CountCommand) -> None:
        if not all((command.count_id, command.product_id, command.location_id, command.reason)):
            raise StockSafetyValidationError("count identifiers, scope, and reason are required")
        if command.observed_quantity < 0 or command.approval_threshold < 0:
            raise StockSafetyValidationError("count quantity and approval threshold cannot be negative")
        if command.counted_at.tzinfo is None:
            raise StockSafetyValidationError("count timestamp must be timezone-aware")

    def _validate_safety(self, command: StockSafetyCommand) -> None:
        if command.action not in self._safety_actions:
            raise StockSafetyValidationError("stock safety action is invalid")
        if not all((command.safety_id, command.product_id, command.location_id, command.reason, command.evidence_id)):
            raise StockSafetyValidationError("safety identifiers, scope, reason, and evidence are required")
        if command.quantity <= 0:
            raise StockSafetyValidationError("safety quantity must be positive")
        if command.occurred_at.tzinfo is None:
            raise StockSafetyValidationError("safety timestamp must be timezone-aware")
