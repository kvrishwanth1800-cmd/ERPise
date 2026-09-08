# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from foundation.access import AuthorizationService, PermissionGrant
from foundation.audit import ApprovalWorkflowService, AuditRecorder, SelfApprovalError
from foundation.organization import ScopeContext
from foundation.procurement import (
    AWARD_WRITE,
    PO_WRITE,
    REQUISITION_APPROVE,
    REQUISITION_WRITE,
    Acknowledgment,
    AdvanceShipmentNotice,
    Award,
    ProcurementService,
    ProcurementStateError,
    ProcurementValidationError,
    PurchaseOrder,
    PurchaseOrderCancellation,
    PurchaseOrderChange,
    PurchaseOrderClosure,
    PurchaseOrderLine,
    Quote,
    Requisition,
)
from foundation.supplier import (
    ContractVersion,
    SupplierCommand,
    SupplierGovernanceService,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
_ACTIONS = (
    REQUISITION_WRITE,
    REQUISITION_APPROVE,
    AWARD_WRITE,
    PO_WRITE,
    "procurement.po.receipt",
    "procurement.po.close",
)


def service(tenant_id: str = "tenant-a") -> tuple[ProcurementService, SupplierGovernanceService, ScopeContext]:
    authorization = AuthorizationService()
    audit = AuditRecorder()
    approvals = ApprovalWorkflowService(audit)
    suppliers = SupplierGovernanceService(authorization, audit, approvals)
    for action in _ACTIONS + ("supplier.write", "supplier.contract.write"):
        authorization.grant(PermissionGrant("buyer", tenant_id, action))
    authorization.assign_duty("buyer", "requester")
    authorization.assign_duty("controller", "approver")
    for action in (REQUISITION_APPROVE,):
        authorization.grant(PermissionGrant("controller", tenant_id, action))
    subject = ProcurementService(authorization, audit, approvals, suppliers)
    scope = ScopeContext(tenant_id)
    return subject, suppliers, scope


def requisition(requisition_id: str = "req-1", tenant_id: str = "tenant-a") -> Requisition:
    return Requisition(
        requisition_id, tenant_id, "buyer", f"idem-{requisition_id}", "office supplies", Decimal("500.00"), "USD", NOW
    )


def quote(quote_id: str, requisition_id: str = "req-1", tenant_id: str = "tenant-a") -> Quote:
    return Quote(quote_id, tenant_id, requisition_id, f"supplier-{quote_id}", Decimal("480.00"), "USD", NOW)


def award(
    award_id: str = "award-1",
    requisition_id: str = "req-1",
    supplier_id: str = "supplier-quote-1",
    compared_quote_ids: tuple[str, ...] = ("quote-1", "quote-2"),
    tenant_id: str = "tenant-a",
) -> Award:
    return Award(
        award_id,
        tenant_id,
        requisition_id,
        supplier_id,
        Decimal("480.00"),
        "USD",
        compared_quote_ids,
        "lowest total cost with acceptable lead time",
        NOW,
    )


def po_line() -> PurchaseOrderLine:
    return PurchaseOrderLine("line-1", "sku-1", Decimal("10"), Decimal("48.00"), Decimal("480.00"))


def purchase_order(
    po_id: str = "po-1",
    award_id: str = "award-1",
    supplier_id: str = "supplier-quote-1",
    contract_id: str = "contract-1",
    idempotency_key: str = "po-idem-1",
    tenant_id: str = "tenant-a",
) -> PurchaseOrder:
    return PurchaseOrder(
        po_id, tenant_id, award_id, supplier_id, contract_id, idempotency_key, (po_line(),), Decimal("480.00"), "USD", NOW
    )


def _register_approved_supplier(
    suppliers: SupplierGovernanceService, scope: ScopeContext, supplier_id: str, contract_id: str
) -> None:
    suppliers.register_supplier(
        "buyer", "session-1", scope, SupplierCommand(supplier_id, "Acme Supplies", "active", "org-1"), "trace-setup"
    )
    suppliers.register_contract_version(
        "buyer",
        "session-1",
        scope,
        ContractVersion(
            f"{contract_id}-v1", scope.tenant_id, contract_id, supplier_id, 1, {"net_terms": "30"}, NOW
        ),
        "trace-setup",
    )
    suppliers.activate_contract_version("buyer", "session-1", scope, f"{contract_id}-v1", NOW, "trace-setup")


def _approved_requisition(subject: ProcurementService, scope: ScopeContext, requisition_id: str = "req-1") -> None:
    subject.submit_requisition("buyer", "session-1", scope, requisition(requisition_id, scope.tenant_id), "trace-1")
    subject.approve_requisition("controller", "session-1", scope, requisition_id, "trace-2")


def test_requisition_requires_approval_before_award() -> None:
    subject, _, scope = service()
    subject.submit_requisition("buyer", "session-1", scope, requisition(), "trace-1")

    with pytest.raises(ProcurementStateError):
        subject.record_award("buyer", "session-1", scope, award(), "trace-2")


def test_requester_cannot_approve_their_own_requisition() -> None:
    subject, _, scope = service()
    subject.submit_requisition("buyer", "session-1", scope, requisition(), "trace-1")

    with pytest.raises(SelfApprovalError):
        subject.approve_requisition("buyer", "session-1", scope, "req-1", "trace-2")


def test_duplicate_requisition_submission_resolves_to_one_outcome() -> None:
    subject, _, scope = service()
    first = subject.submit_requisition("buyer", "session-1", scope, requisition(), "trace-1")
    duplicate = requisition("req-9")
    duplicate = Requisition(
        duplicate.requisition_id,
        duplicate.tenant_id,
        duplicate.requester_id,
        first.idempotency_key,
        duplicate.description,
        duplicate.estimated_amount,
        duplicate.currency,
        duplicate.submitted_at,
    )

    second = subject.submit_requisition("buyer", "session-1", scope, duplicate, "trace-2")

    assert second.requisition_id == first.requisition_id


def test_award_retains_compared_quotes_and_rationale() -> None:
    subject, _, scope = service()
    _approved_requisition(subject, scope)
    subject.record_quote("buyer", "session-1", scope, quote("quote-1"), "trace-1")
    subject.record_quote("buyer", "session-1", scope, quote("quote-2"), "trace-2")

    recorded = subject.record_award("buyer", "session-1", scope, award(), "trace-3")

    assert recorded.compared_quote_ids == ("quote-1", "quote-2")
    assert recorded.rationale


def test_award_requires_at_least_one_compared_quote() -> None:
    subject, _, scope = service()
    _approved_requisition(subject, scope)

    with pytest.raises(ProcurementValidationError):
        subject.record_award("buyer", "session-1", scope, award(compared_quote_ids=()), "trace-3")


def test_issue_purchase_order_requires_active_supplier_and_effective_contract() -> None:
    subject, suppliers, scope = service()
    _approved_requisition(subject, scope)
    subject.record_quote("buyer", "session-1", scope, quote("quote-1"), "trace-1")
    subject.record_quote("buyer", "session-1", scope, quote("quote-2"), "trace-2")
    subject.record_award("buyer", "session-1", scope, award(), "trace-3")

    with pytest.raises(ProcurementValidationError):
        subject.issue_purchase_order("buyer", "session-1", scope, purchase_order(), "trace-4")


def test_issue_purchase_order_succeeds_once_supplier_and_contract_are_effective() -> None:
    subject, suppliers, scope = service()
    _approved_requisition(subject, scope)
    subject.record_quote("buyer", "session-1", scope, quote("quote-1"), "trace-1")
    subject.record_quote("buyer", "session-1", scope, quote("quote-2"), "trace-2")
    subject.record_award("buyer", "session-1", scope, award(), "trace-3")
    _register_approved_supplier(suppliers, scope, "supplier-quote-1", "contract-1")

    issued = subject.issue_purchase_order("buyer", "session-1", scope, purchase_order(), "trace-4")

    assert issued.status == "issued"
    assert subject.history(scope, "po-1")[0].transition == "issued"


def test_duplicate_purchase_order_reference_resolves_to_one_outcome() -> None:
    subject, suppliers, scope = service()
    _approved_requisition(subject, scope)
    subject.record_quote("buyer", "session-1", scope, quote("quote-1"), "trace-1")
    subject.record_quote("buyer", "session-1", scope, quote("quote-2"), "trace-2")
    subject.record_award("buyer", "session-1", scope, award(), "trace-3")
    _register_approved_supplier(suppliers, scope, "supplier-quote-1", "contract-1")
    first = subject.issue_purchase_order("buyer", "session-1", scope, purchase_order(), "trace-4")

    duplicate = purchase_order(po_id="po-9")
    second = subject.issue_purchase_order("buyer", "session-1", scope, duplicate, "trace-5")

    assert second.po_id == first.po_id
    assert subject.history(scope, "po-9") == ()


def _issued_po(subject: ProcurementService, suppliers: SupplierGovernanceService, scope: ScopeContext) -> PurchaseOrder:
    _approved_requisition(subject, scope)
    subject.record_quote("buyer", "session-1", scope, quote("quote-1"), "trace-1")
    subject.record_quote("buyer", "session-1", scope, quote("quote-2"), "trace-2")
    subject.record_award("buyer", "session-1", scope, award(), "trace-3")
    _register_approved_supplier(suppliers, scope, "supplier-quote-1", "contract-1")
    return subject.issue_purchase_order("buyer", "session-1", scope, purchase_order(), "trace-4")


def test_acknowledgment_transitions_purchase_order() -> None:
    subject, suppliers, scope = service()
    _issued_po(subject, suppliers, scope)

    acknowledged = subject.acknowledge(
        "buyer", "session-1", scope, Acknowledgment("ack-1", "tenant-a", "po-1", "supplier-ref-1", NOW), "trace-5"
    )

    assert acknowledged.status == "acknowledged"


def test_partial_receipt_keeps_purchase_order_open_until_fully_received() -> None:
    subject, suppliers, scope = service()
    _issued_po(subject, suppliers, scope)
    subject.acknowledge(
        "buyer", "session-1", scope, Acknowledgment("ack-1", "tenant-a", "po-1", "supplier-ref-1", NOW), "trace-5"
    )

    partial = subject.receive(
        "buyer",
        "session-1",
        scope,
        AdvanceShipmentNotice("asn-1", "tenant-a", "po-1", "line-1", Decimal("4"), "first shipment", NOW),
        "trace-6",
    )
    assert partial.status == "partially_received"

    completed = subject.receive(
        "buyer",
        "session-1",
        scope,
        AdvanceShipmentNotice("asn-2", "tenant-a", "po-1", "line-1", Decimal("6"), "final shipment", NOW),
        "trace-7",
    )
    assert completed.status == "received"
    assert subject.history(scope, "po-1")[-1].transition == "received"


def test_receipt_cannot_exceed_ordered_quantity() -> None:
    subject, suppliers, scope = service()
    _issued_po(subject, suppliers, scope)
    subject.acknowledge(
        "buyer", "session-1", scope, Acknowledgment("ack-1", "tenant-a", "po-1", "supplier-ref-1", NOW), "trace-5"
    )

    with pytest.raises(ProcurementValidationError):
        subject.receive(
            "buyer",
            "session-1",
            scope,
            AdvanceShipmentNotice("asn-1", "tenant-a", "po-1", "line-1", Decimal("11"), "overshipment", NOW),
            "trace-6",
        )


def test_closure_requires_full_receipt() -> None:
    subject, suppliers, scope = service()
    _issued_po(subject, suppliers, scope)
    subject.acknowledge(
        "buyer", "session-1", scope, Acknowledgment("ack-1", "tenant-a", "po-1", "supplier-ref-1", NOW), "trace-5"
    )

    with pytest.raises(ProcurementStateError):
        subject.close(
            "buyer", "session-1", scope, PurchaseOrderClosure("closure-1", "tenant-a", "po-1", "early close", NOW), "trace-6"
        )


def test_lifecycle_history_is_chronological_through_closure() -> None:
    subject, suppliers, scope = service()
    _issued_po(subject, suppliers, scope)
    subject.acknowledge(
        "buyer", "session-1", scope, Acknowledgment("ack-1", "tenant-a", "po-1", "supplier-ref-1", NOW), "trace-5"
    )
    subject.receive(
        "buyer",
        "session-1",
        scope,
        AdvanceShipmentNotice("asn-1", "tenant-a", "po-1", "line-1", Decimal("10"), "full shipment", NOW),
        "trace-6",
    )
    subject.close(
        "buyer", "session-1", scope, PurchaseOrderClosure("closure-1", "tenant-a", "po-1", "complete", NOW), "trace-7"
    )

    transitions = tuple(entry.transition for entry in subject.history(scope, "po-1"))
    assert transitions == ("issued", "acknowledged", "received", "closed")


def test_purchase_order_change_is_recorded_without_altering_status() -> None:
    subject, suppliers, scope = service()
    issued = _issued_po(subject, suppliers, scope)

    subject.change_purchase_order(
        "buyer", "session-1", scope, PurchaseOrderChange("change-1", "tenant-a", "po-1", "delivery date moved", NOW), "trace-5"
    )

    assert subject.purchase_order(scope, "po-1").status == issued.status
    assert subject.history(scope, "po-1")[-1].transition == "changed"


def test_cancelled_purchase_order_cannot_be_changed() -> None:
    subject, suppliers, scope = service()
    _issued_po(subject, suppliers, scope)
    subject.cancel(
        "buyer", "session-1", scope, PurchaseOrderCancellation("cancel-1", "tenant-a", "po-1", "budget withdrawn", NOW), "trace-5"
    )

    with pytest.raises(ProcurementStateError):
        subject.change_purchase_order(
            "buyer", "session-1", scope, PurchaseOrderChange("change-1", "tenant-a", "po-1", "too late", NOW), "trace-6"
        )


def test_purchase_orders_are_isolated_between_tenants() -> None:
    subject, suppliers, scope = service("tenant-a")
    _issued_po(subject, suppliers, scope)
    other_scope = ScopeContext("tenant-b")

    with pytest.raises(ProcurementValidationError):
        subject.purchase_order(other_scope, "po-1")


def test_unauthorized_procurement_command_is_denied() -> None:
    authorization = AuthorizationService()
    audit = AuditRecorder()
    approvals = ApprovalWorkflowService(audit)
    suppliers = SupplierGovernanceService(authorization, audit, approvals)
    subject = ProcurementService(authorization, audit, approvals, suppliers)
    scope = ScopeContext("tenant-a")

    with pytest.raises(PermissionError):
        subject.submit_requisition("buyer", "session-1", scope, requisition(), "trace-1")
