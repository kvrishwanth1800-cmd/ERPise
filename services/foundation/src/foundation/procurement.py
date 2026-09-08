# ruff: noqa: E501
"""Tenant-scoped procurement lifecycle: requisition, award, purchase order, receipt.

Procurement composes budget-gated requisitions with evidence-backed supplier
awards (ADR-001: evidence-backed purchase award) and verifies the awarded
supplier is approved and under an effective contract, through
``SupplierGovernanceService``, before it issues a purchase order (Supplier,
Contracts and Procurement ADR-001: govern suppliers before buying). Purchase
order references are idempotent so a duplicate submission resolves to one
outcome, and lifecycle facts (issue, acknowledgment, partial receipt, change,
closure) are appended in chronological order as durable history evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import ApprovalWorkflowService, AuditRecorder
from foundation.organization import ScopeContext
from foundation.supplier import SupplierGovernanceService

REQUISITION_WRITE = "procurement.requisition.write"
REQUISITION_APPROVE = "procurement.requisition.approve"
AWARD_WRITE = "procurement.award.write"
PO_WRITE = "procurement.po.write"
PO_RECEIPT_WRITE = "procurement.po.receipt"
PO_CLOSE = "procurement.po.close"
REQUISITION_APPROVAL_POLICY = "procurement.requisition.dual-control"


class ProcurementValidationError(ValueError):
    """Raised when a procurement command does not meet the procurement contract."""


class ProcurementStateError(ProcurementValidationError):
    """Raised when a lifecycle transition is invalid for the current state."""


@dataclass(frozen=True)
class Requisition:
    requisition_id: str
    tenant_id: str
    requester_id: str
    idempotency_key: str
    description: str
    estimated_amount: Decimal
    currency: str
    submitted_at: datetime
    status: str = "submitted"


@dataclass(frozen=True)
class Quote:
    quote_id: str
    tenant_id: str
    requisition_id: str
    supplier_id: str
    amount: Decimal
    currency: str
    submitted_at: datetime


@dataclass(frozen=True)
class Award:
    award_id: str
    tenant_id: str
    requisition_id: str
    supplier_id: str
    awarded_amount: Decimal
    currency: str
    compared_quote_ids: tuple[str, ...]
    rationale: str
    awarded_at: datetime


@dataclass(frozen=True)
class PurchaseOrderLine:
    line_id: str
    product_id: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    po_id: str
    tenant_id: str
    award_id: str
    supplier_id: str
    contract_id: str
    idempotency_key: str
    lines: tuple[PurchaseOrderLine, ...]
    total_amount: Decimal
    currency: str
    issued_at: datetime
    status: str = "issued"


@dataclass(frozen=True)
class Acknowledgment:
    acknowledgment_id: str
    tenant_id: str
    po_id: str
    supplier_reference: str
    acknowledged_at: datetime


@dataclass(frozen=True)
class AdvanceShipmentNotice:
    asn_id: str
    tenant_id: str
    po_id: str
    line_id: str
    quantity_received: Decimal
    reason: str
    received_at: datetime


@dataclass(frozen=True)
class PurchaseOrderChange:
    change_id: str
    tenant_id: str
    po_id: str
    reason: str
    changed_at: datetime


@dataclass(frozen=True)
class PurchaseOrderClosure:
    closure_id: str
    tenant_id: str
    po_id: str
    reason: str
    closed_at: datetime


@dataclass(frozen=True)
class PurchaseOrderCancellation:
    cancellation_id: str
    tenant_id: str
    po_id: str
    reason: str
    cancelled_at: datetime


@dataclass(frozen=True)
class PurchaseOrderHistoryEntry:
    entry_id: str
    tenant_id: str
    po_id: str
    sequence: int
    transition: str
    from_status: str
    to_status: str
    detail: str
    occurred_at: datetime


@dataclass(frozen=True)
class ProcurementEvent:
    event_type: str
    tenant_id: str
    subject_id: str


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "issued": frozenset({"acknowledged", "cancelled"}),
    "acknowledged": frozenset({"partially_received", "received", "cancelled"}),
    "partially_received": frozenset({"partially_received", "received"}),
    "received": frozenset({"closed"}),
    "cancelled": frozenset(),
    "closed": frozenset(),
}


class ProcurementService:
    """Runs the requisition-to-closure procurement lifecycle over committed facts."""

    def __init__(
        self,
        authorization: AuthorizationService,
        audit: AuditRecorder,
        approvals: ApprovalWorkflowService,
        suppliers: SupplierGovernanceService,
    ) -> None:
        self._authorization = authorization
        self._audit = audit
        self._approvals = approvals
        self._suppliers = suppliers
        self._requisitions: dict[tuple[str, str], Requisition] = {}
        self._requisitions_by_key: dict[tuple[str, str], str] = {}
        self._quotes: dict[tuple[str, str], Quote] = {}
        self._awards: dict[tuple[str, str], Award] = {}
        self._purchase_orders: dict[tuple[str, str], PurchaseOrder] = {}
        self._purchase_orders_by_key: dict[tuple[str, str], str] = {}
        self._acknowledgments: dict[tuple[str, str], Acknowledgment] = {}
        self._asns: dict[tuple[str, str], AdvanceShipmentNotice] = {}
        self._changes: dict[tuple[str, str], PurchaseOrderChange] = {}
        self._closures: dict[tuple[str, str], PurchaseOrderClosure] = {}
        self._cancellations: dict[tuple[str, str], PurchaseOrderCancellation] = {}
        self._received_quantities: dict[tuple[str, str, str], Decimal] = {}
        self._history: dict[tuple[str, str], list[PurchaseOrderHistoryEntry]] = {}
        self.outbox: list[ProcurementEvent] = []

    # Requisition and budget/approval gate

    def submit_requisition(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        requisition: Requisition,
        trace_id: str,
    ) -> Requisition:
        """Submits one requisition and opens its approval request; a retry with the same key returns that outcome."""
        self._authorization.authorize(principal_id, session_id, scope, REQUISITION_WRITE)
        key = (scope.tenant_id, requisition.idempotency_key)
        existing_id = self._requisitions_by_key.get(key)
        if existing_id is not None:
            return self._requisitions[(scope.tenant_id, existing_id)]
        self._validate_requisition(requisition, scope)
        if (scope.tenant_id, requisition.requisition_id) in self._requisitions:
            raise ProcurementValidationError("requisition identifiers are immutable")
        self._requisitions[(scope.tenant_id, requisition.requisition_id)] = requisition
        self._requisitions_by_key[key] = requisition.requisition_id
        self._approvals.create(
            approval_id=f"{scope.tenant_id}:{requisition.requisition_id}",
            requester_id=requisition.requester_id,
            policy=REQUISITION_APPROVAL_POLICY,
            timeout_outcome="escalate",
            trace_id=trace_id,
        )
        self._record(principal_id, REQUISITION_WRITE, "procurement.requisition.submit", trace_id, "submitted")
        self.outbox.append(ProcurementEvent("RequisitionChanged", scope.tenant_id, requisition.requisition_id))
        return requisition

    def approve_requisition(
        self,
        approver_id: str,
        session_id: str,
        scope: ScopeContext,
        requisition_id: str,
        trace_id: str,
    ) -> Requisition:
        """Approves a requisition; requesters cannot resolve their own approval."""
        self._authorization.authorize(approver_id, session_id, scope, REQUISITION_APPROVE)
        requisition = self.requisition(scope, requisition_id)
        if requisition.status == "approved":
            return requisition
        self._approvals.approve(f"{scope.tenant_id}:{requisition_id}", approver_id, trace_id)
        approved = replace(requisition, status="approved")
        self._requisitions[(scope.tenant_id, requisition_id)] = approved
        self._record(approver_id, REQUISITION_APPROVE, "procurement.requisition.approve", trace_id, "approved")
        self.outbox.append(ProcurementEvent("RequisitionChanged", scope.tenant_id, requisition_id))
        return approved

    def requisition(self, scope: ScopeContext, requisition_id: str) -> Requisition:
        record = self._requisitions.get((scope.tenant_id, requisition_id))
        if record is None:
            raise ProcurementValidationError("requisition is outside the authorized tenant scope")
        return record

    # RFQ / quote comparison evidence

    def record_quote(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        quote: Quote,
        trace_id: str,
    ) -> Quote:
        self._authorization.authorize(principal_id, session_id, scope, AWARD_WRITE)
        requisition = self.requisition(scope, quote.requisition_id)
        if requisition.status != "approved":
            raise ProcurementStateError("quotes require an approved requisition")
        self._validate_quote(quote, scope)
        key = (scope.tenant_id, quote.quote_id)
        if key in self._quotes:
            raise ProcurementValidationError("quote identifiers are immutable")
        self._quotes[key] = quote
        self._record(principal_id, AWARD_WRITE, "procurement.quote.record", trace_id, "recorded")
        return quote

    def quote(self, scope: ScopeContext, quote_id: str) -> Quote:
        record = self._quotes.get((scope.tenant_id, quote_id))
        if record is None:
            raise ProcurementValidationError("quote is outside the authorized tenant scope")
        return record

    # Evidence-backed award (ADR-001: evidence-backed purchase award)

    def record_award(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        award: Award,
        trace_id: str,
    ) -> Award:
        """Awards a requisition to a supplier; retains the compared quotes and a rationale as evidence."""
        self._authorization.authorize(principal_id, session_id, scope, AWARD_WRITE)
        requisition = self.requisition(scope, award.requisition_id)
        if requisition.status != "approved":
            raise ProcurementStateError("an award requires an approved requisition")
        if not award.compared_quote_ids:
            raise ProcurementValidationError("an award must retain the compared quotation evidence")
        for quote_id in award.compared_quote_ids:
            self.quote(scope, quote_id)
        if not award.rationale:
            raise ProcurementValidationError("an award must record its selection rationale")
        if award.tenant_id != scope.tenant_id or award.awarded_amount <= 0 or award.awarded_at.tzinfo is None:
            raise ProcurementValidationError(
                "awards must be scoped to the authorized tenant, positive, and timezone-aware"
            )
        key = (scope.tenant_id, award.award_id)
        if key in self._awards:
            raise ProcurementValidationError("award identifiers are immutable")
        self._awards[key] = award
        self._record(principal_id, AWARD_WRITE, "procurement.award.record", trace_id, "awarded")
        self.outbox.append(ProcurementEvent("PurchaseOrderChanged", scope.tenant_id, award.award_id))
        return award

    def award(self, scope: ScopeContext, award_id: str) -> Award:
        record = self._awards.get((scope.tenant_id, award_id))
        if record is None:
            raise ProcurementValidationError("award is outside the authorized tenant scope")
        return record

    # Purchase order issuance (checks approved supplier and effective contract first)

    def issue_purchase_order(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        po: PurchaseOrder,
        trace_id: str,
    ) -> PurchaseOrder:
        """Issues one purchase order; a retry with the same idempotency key returns that outcome.

        Verifies the supplier is approved and the contract is effective through
        ``SupplierGovernanceService`` before creating the purchase order.
        """
        self._authorization.authorize(principal_id, session_id, scope, PO_WRITE)
        key = (scope.tenant_id, po.idempotency_key)
        existing_id = self._purchase_orders_by_key.get(key)
        if existing_id is not None:
            return self._purchase_orders[(scope.tenant_id, existing_id)]
        award = self.award(scope, po.award_id)
        if award.supplier_id != po.supplier_id:
            raise ProcurementValidationError("a purchase order supplier must match the awarded supplier")
        supplier = self._suppliers.supplier(scope, po.supplier_id)
        if not supplier.permits_dependent_operations:
            raise ProcurementValidationError("purchase orders require an active, approved supplier")
        effective_terms = self._suppliers.effective_terms(scope, po.contract_id, po.issued_at)
        if effective_terms is None:
            raise ProcurementValidationError("purchase orders require an effective supplier contract")
        self._validate_po(po, scope)
        if (scope.tenant_id, po.po_id) in self._purchase_orders:
            raise ProcurementValidationError("purchase order identifiers are immutable")
        self._purchase_orders[(scope.tenant_id, po.po_id)] = po
        self._purchase_orders_by_key[key] = po.po_id
        self._append_history(scope, po.po_id, "issued", "new", "issued", f"award:{po.award_id}", po.issued_at)
        self._record(principal_id, PO_WRITE, "procurement.po.issue", trace_id, "issued")
        self.outbox.append(ProcurementEvent("PurchaseOrderChanged", scope.tenant_id, po.po_id))
        return po

    def acknowledge(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        acknowledgment: Acknowledgment,
        trace_id: str,
    ) -> PurchaseOrder:
        self._authorization.authorize(principal_id, session_id, scope, PO_WRITE)
        po = self.purchase_order(scope, acknowledgment.po_id)
        self._require_transition(po, "acknowledged")
        if (
            not all((acknowledgment.acknowledgment_id, acknowledgment.supplier_reference))
            or acknowledgment.tenant_id != scope.tenant_id
            or acknowledgment.acknowledged_at.tzinfo is None
        ):
            raise ProcurementValidationError(
                "acknowledgments require identity, a supplier reference, and timezone-aware time"
            )
        key = (scope.tenant_id, acknowledgment.acknowledgment_id)
        if key in self._acknowledgments:
            raise ProcurementValidationError("acknowledgment identifiers are immutable")
        self._acknowledgments[key] = acknowledgment
        updated = replace(po, status="acknowledged")
        self._purchase_orders[(scope.tenant_id, po.po_id)] = updated
        self._append_history(
            scope, po.po_id, "acknowledged", po.status, "acknowledged",
            acknowledgment.supplier_reference, acknowledgment.acknowledged_at,
        )
        self._record(principal_id, PO_WRITE, "procurement.po.acknowledge", trace_id, "acknowledged")
        self.outbox.append(ProcurementEvent("PurchaseOrderChanged", scope.tenant_id, po.po_id))
        return updated

    def change_purchase_order(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        change: PurchaseOrderChange,
        trace_id: str,
    ) -> PurchaseOrder:
        """Records a purchase order change as append-only lifecycle evidence."""
        self._authorization.authorize(principal_id, session_id, scope, PO_WRITE)
        po = self.purchase_order(scope, change.po_id)
        if po.status in ("cancelled", "closed"):
            raise ProcurementStateError("closed or cancelled purchase orders cannot be changed")
        if (
            not all((change.change_id, change.reason))
            or change.tenant_id != scope.tenant_id
            or change.changed_at.tzinfo is None
        ):
            raise ProcurementValidationError(
                "purchase order changes require identity, a reason, and timezone-aware time"
            )
        key = (scope.tenant_id, change.change_id)
        if key in self._changes:
            raise ProcurementValidationError("purchase order change identifiers are immutable")
        self._changes[key] = change
        self._append_history(scope, po.po_id, "changed", po.status, po.status, change.reason, change.changed_at)
        self._record(principal_id, PO_WRITE, "procurement.po.change", trace_id, "changed")
        self.outbox.append(ProcurementEvent("PurchaseOrderChanged", scope.tenant_id, po.po_id))
        return po

    def receive(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        asn: AdvanceShipmentNotice,
        trace_id: str,
    ) -> PurchaseOrder:
        """Records one partial or final receipt against an ordered line."""
        self._authorization.authorize(principal_id, session_id, scope, PO_RECEIPT_WRITE)
        po = self.purchase_order(scope, asn.po_id)
        line = self._line(po, asn.line_id)
        if (
            not all((asn.asn_id, asn.reason))
            or asn.tenant_id != scope.tenant_id
            or asn.quantity_received <= 0
            or asn.received_at.tzinfo is None
        ):
            raise ProcurementValidationError(
                "receipts require identity, a positive quantity, a reason, and timezone-aware time"
            )
        key = (scope.tenant_id, asn.asn_id)
        if key in self._asns:
            raise ProcurementValidationError("receipt identifiers are immutable")
        received_key = (scope.tenant_id, po.po_id, asn.line_id)
        already_received = self._received_quantities.get(received_key, Decimal(0))
        total_received = already_received + asn.quantity_received
        if total_received > line.quantity:
            raise ProcurementValidationError("received quantity cannot exceed the ordered quantity")
        fully_received = all(
            (
                self._received_quantities.get((scope.tenant_id, po.po_id, candidate.line_id), Decimal(0))
                + (asn.quantity_received if candidate.line_id == asn.line_id else Decimal(0))
            )
            >= candidate.quantity
            for candidate in po.lines
        )
        new_status = "received" if fully_received else "partially_received"
        self._require_transition(po, new_status)
        self._asns[key] = asn
        self._received_quantities[received_key] = total_received
        updated = replace(po, status=new_status)
        self._purchase_orders[(scope.tenant_id, po.po_id)] = updated
        self._append_history(scope, po.po_id, new_status, po.status, new_status, asn.reason, asn.received_at)
        self._record(principal_id, PO_RECEIPT_WRITE, "procurement.po.receive", trace_id, new_status)
        self.outbox.append(ProcurementEvent("AsnReceived", scope.tenant_id, asn.asn_id))
        return updated

    def close(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        closure: PurchaseOrderClosure,
        trace_id: str,
    ) -> PurchaseOrder:
        self._authorization.authorize(principal_id, session_id, scope, PO_CLOSE)
        po = self.purchase_order(scope, closure.po_id)
        self._require_transition(po, "closed")
        if (
            not all((closure.closure_id, closure.reason))
            or closure.tenant_id != scope.tenant_id
            or closure.closed_at.tzinfo is None
        ):
            raise ProcurementValidationError("closures require identity, a reason, and timezone-aware time")
        key = (scope.tenant_id, closure.closure_id)
        if key in self._closures:
            raise ProcurementValidationError("closure identifiers are immutable")
        self._closures[key] = closure
        updated = replace(po, status="closed")
        self._purchase_orders[(scope.tenant_id, po.po_id)] = updated
        self._append_history(scope, po.po_id, "closed", po.status, "closed", closure.reason, closure.closed_at)
        self._record(principal_id, PO_CLOSE, "procurement.po.close", trace_id, "closed")
        self.outbox.append(ProcurementEvent("PurchaseOrderChanged", scope.tenant_id, po.po_id))
        return updated

    def cancel(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        cancellation: PurchaseOrderCancellation,
        trace_id: str,
    ) -> PurchaseOrder:
        self._authorization.authorize(principal_id, session_id, scope, PO_WRITE)
        po = self.purchase_order(scope, cancellation.po_id)
        self._require_transition(po, "cancelled")
        if (
            not all((cancellation.cancellation_id, cancellation.reason))
            or cancellation.tenant_id != scope.tenant_id
            or cancellation.cancelled_at.tzinfo is None
        ):
            raise ProcurementValidationError("cancellations require identity, a reason, and timezone-aware time")
        key = (scope.tenant_id, cancellation.cancellation_id)
        if key in self._cancellations:
            raise ProcurementValidationError("cancellation identifiers are immutable")
        self._cancellations[key] = cancellation
        updated = replace(po, status="cancelled")
        self._purchase_orders[(scope.tenant_id, po.po_id)] = updated
        self._append_history(
            scope, po.po_id, "cancelled", po.status, "cancelled", cancellation.reason, cancellation.cancelled_at
        )
        self._record(principal_id, PO_WRITE, "procurement.po.cancel", trace_id, "cancelled")
        self.outbox.append(ProcurementEvent("PurchaseOrderChanged", scope.tenant_id, po.po_id))
        return updated

    def purchase_order(self, scope: ScopeContext, po_id: str) -> PurchaseOrder:
        record = self._purchase_orders.get((scope.tenant_id, po_id))
        if record is None:
            raise ProcurementValidationError("purchase order is outside the authorized tenant scope")
        return record

    def history(self, scope: ScopeContext, po_id: str) -> tuple[PurchaseOrderHistoryEntry, ...]:
        """Returns the purchase order's recorded transitions in chronological order."""
        return tuple(self._history.get((scope.tenant_id, po_id), []))

    # Internal helpers

    def _record(self, actor_id: str, authority: str, source: str, trace_id: str, result: str) -> None:
        self._audit.record(actor_id, authority, source, "policy-controlled procurement action", authority, trace_id, result)

    def _require_transition(self, po: PurchaseOrder, to_status: str) -> None:
        if to_status not in _ALLOWED_TRANSITIONS.get(po.status, frozenset()):
            raise ProcurementStateError(f"cannot transition a purchase order from {po.status} to {to_status}")

    @staticmethod
    def _line(po: PurchaseOrder, line_id: str) -> PurchaseOrderLine:
        for line in po.lines:
            if line.line_id == line_id:
                return line
        raise ProcurementValidationError("line is not part of the purchase order")

    def _append_history(
        self,
        scope: ScopeContext,
        po_id: str,
        transition: str,
        from_status: str,
        to_status: str,
        detail: str,
        occurred_at: datetime,
    ) -> None:
        entries = self._history.setdefault((scope.tenant_id, po_id), [])
        entry = PurchaseOrderHistoryEntry(
            f"{po_id}-{len(entries) + 1}",
            scope.tenant_id,
            po_id,
            len(entries) + 1,
            transition,
            from_status,
            to_status,
            detail,
            occurred_at,
        )
        entries.append(entry)

    @staticmethod
    def _validate_requisition(requisition: Requisition, scope: ScopeContext) -> None:
        if not all(
            (requisition.requisition_id, requisition.requester_id, requisition.idempotency_key, requisition.description)
        ):
            raise ProcurementValidationError(
                "requisitions require identity, a requester, an idempotency key, and a description"
            )
        if requisition.tenant_id != scope.tenant_id:
            raise ProcurementValidationError("requisitions must be scoped to the authorized tenant")
        if requisition.estimated_amount <= 0:
            raise ProcurementValidationError("requisitions require a positive estimated amount")
        if requisition.submitted_at.tzinfo is None:
            raise ProcurementValidationError("requisition timestamps must be timezone-aware")

    @staticmethod
    def _validate_quote(quote: Quote, scope: ScopeContext) -> None:
        if not all((quote.quote_id, quote.supplier_id)):
            raise ProcurementValidationError("quotes require identity and a supplier")
        if quote.tenant_id != scope.tenant_id:
            raise ProcurementValidationError("quotes must be scoped to the authorized tenant")
        if quote.amount <= 0:
            raise ProcurementValidationError("quotes require a positive amount")
        if quote.submitted_at.tzinfo is None:
            raise ProcurementValidationError("quote timestamps must be timezone-aware")

    @staticmethod
    def _validate_po(po: PurchaseOrder, scope: ScopeContext) -> None:
        if not all((po.po_id, po.award_id, po.supplier_id, po.contract_id, po.idempotency_key)) or not po.lines:
            raise ProcurementValidationError(
                "purchase orders require identity, award, supplier, contract, and at least one line"
            )
        if po.tenant_id != scope.tenant_id:
            raise ProcurementValidationError("purchase orders must be scoped to the authorized tenant")
        if po.total_amount != sum((line.line_total for line in po.lines), Decimal()):
            raise ProcurementValidationError("purchase order total must equal the sum of its lines")
        if po.issued_at.tzinfo is None:
            raise ProcurementValidationError("purchase order timestamps must be timezone-aware")
