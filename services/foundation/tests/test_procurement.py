# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from foundation.access import AuthorizationService, PermissionGrant, SessionRevocationService
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
    authorization = AuthorizationService(SessionRevocationService())
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
