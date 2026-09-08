# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    ConflictingDutyError,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.audit import ApprovalWorkflowService, AuditRecorder, SelfApprovalError
from foundation.organization import ScopeContext
from foundation.supplier import (
    BANK_CHANGE_APPROVE,
    BANK_CHANGE_REQUEST,
    CONTRACT_WRITE,
    SUPPLIER_WRITE,
    BankChangeStateError,
    BankDetails,
    Certification,
    ContractVersion,
    SupplierCommand,
    SupplierGovernanceService,
    SupplierScopeError,
    SupplierValidationError,
)

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
TODAY = date(2026, 9, 8)
BANK = BankDetails("Acme Supplies", "12345678", "BANKGB2L")
OTHER_BANK = BankDetails("Acme Supplies", "87654321", "BANKGB2L")


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def service(tenant_id: str = "tenant-a") -> SupplierGovernanceService:
    authorization = AuthorizationService(SessionRevocationService())
    for action in (SUPPLIER_WRITE, CONTRACT_WRITE, BANK_CHANGE_REQUEST):
        authorization.grant(PermissionGrant("buyer", tenant_id, action))
    authorization.grant(PermissionGrant("controller", tenant_id, BANK_CHANGE_APPROVE))
    authorization.assign_duty("buyer", "requester")
    authorization.assign_duty("controller", "approver")
    audit = AuditRecorder()
    return SupplierGovernanceService(authorization, audit, ApprovalWorkflowService(audit))


def supplier(subject: SupplierGovernanceService, supplier_id: str = "supplier-1") -> None:
    subject.register_supplier(
        "buyer",
        "session",
        scope(),
        SupplierCommand(supplier_id, "Acme Supplies", "active", "org-north"),
        "register-trace",
    )


def request_bank_change(
    subject: SupplierGovernanceService,
    request_id: str = "bank-1",
    idempotency_key: str = "idem-bank-1",
    proposed: BankDetails = BANK,
) -> None:
    subject.request_bank_change(
        "buyer",
        "session",
        scope(),
        "supplier-1",
        request_id,
        proposed,
        idempotency_key,
        NOW,
        "bank-request-trace",
    )


def version(
    version_id: str,
    number: int,
    terms: dict[str, str],
    effective_from: datetime,
    expires_on: date | None = None,
) -> ContractVersion:
    return ContractVersion(
        version_id,
        "tenant-a",
        "contract-1",
        "supplier-1",
        number,
        terms,
        effective_from,
        expires_on,
    )


def test_bank_data_stays_inactive_until_an_eligible_separate_approver_approves() -> None:
    subject = service()
    supplier(subject)
    request_bank_change(subject)

    assert subject.active_bank_details(scope(), "supplier-1") is None
    with pytest.raises(SelfApprovalError):
        subject.approve_bank_change("buyer", "session", scope(), "bank-1", NOW, "self-trace")
    assert subject.active_bank_details(scope(), "supplier-1") is None

    approved = subject.approve_bank_change(
        "controller", "session", scope(), "bank-1", NOW, "approve-trace"
    )
    assert approved.is_active
    assert approved.approver_id == "controller"
    assert approved.requester_id != approved.approver_id
    assert subject.active_bank_details(scope(), "supplier-1") == BANK


def test_bank_change_approval_requires_the_eligible_approval_permission() -> None:
    subject = service()
    supplier(subject)
    request_bank_change(subject)

    with pytest.raises(AuthorizationDeniedError):
        subject.approve_bank_change("clerk", "session", scope(), "bank-1", NOW, "denied-trace")
    assert subject.active_bank_details(scope(), "supplier-1") is None


def test_requester_and_approver_duties_cannot_be_held_by_one_principal() -> None:
    authorization = AuthorizationService(SessionRevocationService())
    authorization.assign_duty("buyer", "requester")
    with pytest.raises(ConflictingDutyError):
        authorization.assign_duty("buyer", "approver")


def test_bank_change_requests_are_idempotent_and_approval_is_replay_safe() -> None:
    subject = service()
    supplier(subject)
    request_bank_change(subject)
    request_bank_change(subject, "bank-2", "idem-bank-1", OTHER_BANK)

    stored = subject.bank_change_request(scope(), "bank-1")
    assert stored.proposed == BANK
    with pytest.raises(SupplierScopeError):
        subject.bank_change_request(scope(), "bank-2")

    first = subject.approve_bank_change(
        "controller", "session", scope(), "bank-1", NOW, "approve-trace"
    )
    second = subject.approve_bank_change(
        "controller", "session", scope(), "bank-1", NOW, "approve-retry"
    )
    assert first == second


def test_invalid_bank_change_commands_are_rejected() -> None:
    subject = service()
    supplier(subject)

    with pytest.raises(SupplierValidationError, match="bank account holder"):
        request_bank_change(subject, "bank-blank", "idem-blank", BankDetails("", "", ""))
    with pytest.raises(SupplierValidationError, match="timezone-aware"):
        subject.request_bank_change(
            "buyer",
            "session",
            scope(),
            "supplier-1",
            "bank-naive",
            BANK,
            "idem-naive",
            datetime(2026, 9, 8, 12),
            "naive-trace",
        )
    request_bank_change(subject)
    with pytest.raises(BankChangeStateError, match="unique"):
        request_bank_change(subject, "bank-1", "idem-other")
    subject.approve_bank_change("controller", "session", scope(), "bank-1", NOW, "approve-trace")


def test_effective_contract_version_preserves_prior_applied_terms() -> None:
    subject = service()
    supplier(subject)
    first = version("version-1", 1, {"payment_terms": "net-30"}, NOW - timedelta(days=10))
    second = version("version-2", 2, {"payment_terms": "net-45"}, NOW)
    subject.register_contract_version("buyer", "session", scope(), first, "register-1")
    subject.register_contract_version("buyer", "session", scope(), second, "register-2")

    subject.activate_contract_version(
        "buyer", "session", scope(), "version-1", NOW - timedelta(days=9), "activate-1"
    )
    assert subject.effective_terms(scope(), "contract-1", NOW - timedelta(days=5)) == {
        "payment_terms": "net-30"
    }

    activated = subject.activate_contract_version(
        "buyer", "session", scope(), "version-2", NOW, "activate-2"
    )
    assert activated.status == "active"
    assert subject.contract_version(scope(), "version-1").status == "superseded"
    assert subject.contract_version(scope(), "version-1").terms == {"payment_terms": "net-30"}
    assert subject.effective_terms(scope(), "contract-1", NOW) == {"payment_terms": "net-45"}
    assert subject.effective_terms(scope(), "contract-1", NOW - timedelta(days=5)) == {
        "payment_terms": "net-30"
    }


def test_contract_activation_rejects_invalid_and_out_of_order_versions() -> None:
    subject = service()
    supplier(subject)
    subject.register_contract_version(
        "buyer", "session", scope(), version("version-1", 1, {"payment_terms": "net-30"}, NOW), "register-1"
    )

    with pytest.raises(SupplierValidationError, match="version numbers must increase"):
        subject.register_contract_version(
            "buyer", "session", scope(), version("version-0", 1, {"t": "v"}, NOW), "register-0"
        )
    with pytest.raises(SupplierValidationError, match="require configured terms"):
        subject.register_contract_version(
            "buyer", "session", scope(), version("version-9", 9, {}, NOW), "register-9"
        )
    with pytest.raises(SupplierValidationError, match="not yet effective"):
        subject.activate_contract_version(
            "buyer", "session", scope(), "version-1", NOW - timedelta(days=1), "early"
        )
    with pytest.raises(SupplierValidationError, match="no contract version is effective"):
        subject.effective_terms(scope(), "contract-1", NOW - timedelta(days=1))


def test_approaching_expiry_notifies_the_responsible_scope_once() -> None:
    subject = service()
    supplier(subject)
    subject.register_certification(
        "buyer",
        "session",
        scope(),
        Certification("cert-1", "tenant-a", "supplier-1", "iso-9001", TODAY + timedelta(days=10)),
        "cert-trace",
    )
    subject.register_certification(
        "buyer",
        "session",
        scope(),
        Certification("cert-2", "tenant-a", "supplier-1", "insurance", TODAY + timedelta(days=400)),
        "cert-trace-2",
    )
    subject.register_contract_version(
        "buyer",
        "session",
        scope(),
        version("version-1", 1, {"payment_terms": "net-30"}, NOW, TODAY + timedelta(days=20)),
        "register-1",
    )
    subject.activate_contract_version("buyer", "session", scope(), "version-1", NOW, "activate-1")

    advisories = subject.advise_expiries(scope(), TODAY, "expiry-trace")
    assert [(advisory.subject_type, advisory.subject_id) for advisory in advisories] == [
        ("certification", "cert-1"),
        ("contract", "version-1"),
    ]
    assert {advisory.responsible_organization_id for advisory in advisories} == {"org-north"}
    assert subject.advise_expiries(scope(), TODAY, "expiry-retry") == ()
    with pytest.raises(SupplierValidationError, match="horizon must be positive"):
        subject.advise_expiries(scope(), TODAY, "expiry-trace", horizon_days=0)


def test_supplier_changes_publish_events_and_retain_audit_evidence() -> None:
    audit = AuditRecorder()
    authorization = AuthorizationService(SessionRevocationService())
    for action in (SUPPLIER_WRITE, CONTRACT_WRITE, BANK_CHANGE_REQUEST):
        authorization.grant(PermissionGrant("buyer", "tenant-a", action))
    authorization.grant(PermissionGrant("controller", "tenant-a", BANK_CHANGE_APPROVE))
    subject = SupplierGovernanceService(authorization, audit, ApprovalWorkflowService(audit))

    supplier(subject)
    request_bank_change(subject)
    subject.approve_bank_change("controller", "session", scope(), "bank-1", NOW, "approve-trace")
    subject.register_contract_version(
        "buyer",
        "session",
        scope(),
        version("version-1", 1, {"payment_terms": "net-30"}, NOW, TODAY + timedelta(days=5)),
        "register-1",
    )
    subject.activate_contract_version("buyer", "session", scope(), "version-1", NOW, "activate-1")
    subject.advise_expiries(scope(), TODAY, "expiry-trace")

    assert [event.event_type for event in subject.outbox] == [
        "SupplierChanged",
        "SupplierChanged",
        "ContractActivated",
        "SupplierExpiryApproaching",
    ]
    sources = [record.source for record in audit.records]
    assert "supplier.bank.request" in sources
    assert "supplier.bank.approve" in sources
    assert "supplier.contract.activate" in sources
    assert "supplier.expiry.contract" in sources
    assert all(record.trace_id for record in audit.records)


def test_supplier_records_are_isolated_between_tenants() -> None:
    subject = service()
    supplier(subject)

    with pytest.raises(SupplierScopeError):
        subject.supplier(scope("tenant-b"), "supplier-1")
    with pytest.raises(AuthorizationDeniedError):
        subject.register_supplier(
            "buyer",
            "session",
            scope("tenant-b"),
            SupplierCommand("supplier-2", "Other", "active", "org-south"),
            "cross-trace",
        )


def test_invalid_supplier_commands_are_rejected() -> None:
    subject = service()

    with pytest.raises(SupplierValidationError, match="identifier and name"):
        subject.register_supplier(
            "buyer", "session", scope(), SupplierCommand("", "", "active", "org-north"), "trace"
        )
    with pytest.raises(SupplierValidationError, match="responsible organization"):
        subject.register_supplier(
            "buyer", "session", scope(), SupplierCommand("s", "Name", "active", ""), "trace"
        )
    with pytest.raises(SupplierValidationError, match="supplier status"):
        subject.register_supplier(
            "buyer", "session", scope(), SupplierCommand("s", "Name", "unknown", "org"), "trace"
        )
    supplier(subject)
    with pytest.raises(SupplierValidationError, match="unique within a tenant"):
        supplier(subject)
    suspended = subject.change_status(
        "buyer", "session", scope(), "supplier-1", "suspended", "status-trace"
    )
    assert not suspended.permits_dependent_operations
