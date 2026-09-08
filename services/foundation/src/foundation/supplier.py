# ruff: noqa: E501
"""Tenant-scoped supplier lifecycle, dual-control bank changes, contracts, and expiry advisories."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from foundation.access import AuthorizationService
from foundation.audit import ApprovalWorkflowService, AuditRecorder
from foundation.organization import ScopeContext

SUPPLIER_WRITE = "supplier.write"
BANK_CHANGE_REQUEST = "supplier.bank.request"
BANK_CHANGE_APPROVE = "supplier.bank.approve"
CONTRACT_WRITE = "supplier.contract.write"
BANK_CHANGE_POLICY = "supplier.bank.dual-control"

SUPPLIER_STATUSES = frozenset({"draft", "active", "suspended", "inactive"})
CONTRACT_STATUSES = frozenset({"registered", "active", "superseded"})
DEFAULT_EXPIRY_HORIZON_DAYS = 30


class SupplierValidationError(ValueError):
    """Raised when a supplier command does not meet the Suppliers and Contracts contract."""


class SupplierScopeError(PermissionError):
    """Raised when a supplier record is outside the authorized tenant scope."""


class BankChangeStateError(ValueError):
    """Raised when a controlled bank change transition is invalid."""


@dataclass(frozen=True)
class BankDetails:
    account_holder: str
    account_number: str
    bank_identifier: str


@dataclass(frozen=True)
class SupplierCommand:
    supplier_id: str
    name: str
    status: str
    responsible_organization_id: str


@dataclass(frozen=True)
class SupplierRecord:
    supplier_id: str
    tenant_id: str
    name: str
    status: str
    responsible_organization_id: str
    active_bank_details: BankDetails | None = None

    @property
    def permits_dependent_operations(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class BankChangeRequest:
    request_id: str
    tenant_id: str
    supplier_id: str
    requester_id: str
    proposed: BankDetails
    idempotency_key: str
    requested_at: datetime
    status: str = "pending"
    approver_id: str | None = None
    decided_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "approved"


@dataclass(frozen=True)
class ContractVersion:
    version_id: str
    tenant_id: str
    contract_id: str
    supplier_id: str
    version_number: int
    terms: Mapping[str, str]
    effective_from: datetime
    expires_on: date | None = None
    status: str = "registered"


@dataclass(frozen=True)
class Certification:
    certification_id: str
    tenant_id: str
    supplier_id: str
    kind: str
    expires_on: date


@dataclass(frozen=True)
class ExpiryAdvisory:
    subject_type: str
    subject_id: str
    supplier_id: str
    responsible_organization_id: str
    expires_on: date


@dataclass(frozen=True)
class SupplierEvent:
    event_type: str
    tenant_id: str
    supplier_id: str
    subject_id: str
    responsible_organization_id: str
    trace_id: str


class SupplierGovernanceService:
    """Owns supplier governance: lifecycle, dual control, effective contracts, and advisories."""

    def __init__(
        self,
        authorization: AuthorizationService,
        audit: AuditRecorder,
        approvals: ApprovalWorkflowService,
    ) -> None:
        self._authorization = authorization
        self._audit = audit
        self._approvals = approvals
        self._suppliers: dict[tuple[str, str], SupplierRecord] = {}
        self._bank_requests: dict[tuple[str, str], BankChangeRequest] = {}
        self._bank_request_keys: dict[tuple[str, str], str] = {}
        self._contract_versions: dict[tuple[str, str], ContractVersion] = {}
        self._certifications: dict[tuple[str, str], Certification] = {}
        self._advised: set[tuple[str, str, str, date]] = set()
        self.outbox: list[SupplierEvent] = []

    # Supplier lifecycle

    def register_supplier(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        command: SupplierCommand,
        trace_id: str,
    ) -> SupplierRecord:
        self._authorization.authorize(principal_id, session_id, scope, SUPPLIER_WRITE)
        self._validate_supplier(command)
        key = (scope.tenant_id, command.supplier_id)
        if key in self._suppliers:
            raise SupplierValidationError("supplier identifiers must be unique within a tenant")
        record = SupplierRecord(
            command.supplier_id,
            scope.tenant_id,
            command.name,
            command.status,
            command.responsible_organization_id,
        )
        self._suppliers[key] = record
        self._record_audit(principal_id, SUPPLIER_WRITE, "supplier.register", trace_id, "registered")
        self._publish("SupplierChanged", record, command.supplier_id, trace_id)
        return record

    def change_status(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        supplier_id: str,
        status: str,
        trace_id: str,
    ) -> SupplierRecord:
        self._authorization.authorize(principal_id, session_id, scope, SUPPLIER_WRITE)
        if status not in SUPPLIER_STATUSES:
            raise SupplierValidationError("supplier status must be draft, active, suspended, or inactive")
        current = self.supplier(scope, supplier_id)
        if current.status == status:
            return current
        updated = SupplierRecord(
            current.supplier_id,
            current.tenant_id,
            current.name,
            status,
            current.responsible_organization_id,
            current.active_bank_details,
        )
        self._suppliers[(scope.tenant_id, supplier_id)] = updated
        self._record_audit(principal_id, SUPPLIER_WRITE, "supplier.status", trace_id, status)
        self._publish("SupplierChanged", updated, supplier_id, trace_id)
        return updated

    def supplier(self, scope: ScopeContext, supplier_id: str) -> SupplierRecord:
        record = self._suppliers.get((scope.tenant_id, supplier_id))
        if record is None:
            raise SupplierScopeError("supplier is outside the authorized tenant scope")
        return record

    # Controlled bank data change

    def request_bank_change(
        self,
        requester_id: str,
        session_id: str,
        scope: ScopeContext,
        supplier_id: str,
        request_id: str,
        proposed: BankDetails,
        idempotency_key: str,
        requested_at: datetime,
        trace_id: str,
    ) -> BankChangeRequest:
        self._authorization.authorize(requester_id, session_id, scope, BANK_CHANGE_REQUEST)
        self.supplier(scope, supplier_id)
        self._validate_bank_details(proposed)
        if not request_id or not idempotency_key:
            raise SupplierValidationError("bank change request and idempotency identifiers are required")
        if requested_at.tzinfo is None:
            raise SupplierValidationError("bank change timestamps must be timezone-aware")
        replayed = self._bank_request_keys.get((scope.tenant_id, idempotency_key))
        if replayed is not None:
            return self._bank_requests[(scope.tenant_id, replayed)]
        if (scope.tenant_id, request_id) in self._bank_requests:
            raise BankChangeStateError("bank change request identifiers must be unique within a tenant")
        self._approvals.create(
            approval_id=f"{scope.tenant_id}:{request_id}",
            requester_id=requester_id,
            policy=BANK_CHANGE_POLICY,
            timeout_outcome="escalate",
            trace_id=trace_id,
        )
        request = BankChangeRequest(
            request_id,
            scope.tenant_id,
            supplier_id,
            requester_id,
            proposed,
            idempotency_key,
            requested_at,
        )
        self._bank_requests[(scope.tenant_id, request_id)] = request
        self._bank_request_keys[(scope.tenant_id, idempotency_key)] = request_id
        self._record_audit(
            requester_id, BANK_CHANGE_POLICY, "supplier.bank.request", trace_id, "pending"
        )
        return request

    def approve_bank_change(
        self,
        approver_id: str,
        session_id: str,
        scope: ScopeContext,
        request_id: str,
        decided_at: datetime,
        trace_id: str,
    ) -> BankChangeRequest:
        self._authorization.authorize(approver_id, session_id, scope, BANK_CHANGE_APPROVE)
        request = self.bank_change_request(scope, request_id)
        if request.status == "approved" and request.approver_id == approver_id:
            return request
        if request.status != "pending":
            raise BankChangeStateError("only pending bank change requests can be approved")
        if decided_at.tzinfo is None:
            raise SupplierValidationError("bank change timestamps must be timezone-aware")
        self._approvals.approve(f"{scope.tenant_id}:{request_id}", approver_id, trace_id)
        approved = BankChangeRequest(
            request.request_id,
            request.tenant_id,
            request.supplier_id,
            request.requester_id,
            request.proposed,
            request.idempotency_key,
            request.requested_at,
            "approved",
            approver_id,
            decided_at,
        )
        self._bank_requests[(scope.tenant_id, request_id)] = approved
        current = self.supplier(scope, request.supplier_id)
        activated = SupplierRecord(
            current.supplier_id,
            current.tenant_id,
            current.name,
            current.status,
            current.responsible_organization_id,
            request.proposed,
        )
        self._suppliers[(scope.tenant_id, request.supplier_id)] = activated
        self._record_audit(
            approver_id, BANK_CHANGE_POLICY, "supplier.bank.approve", trace_id, "approved"
        )
        self._publish("SupplierChanged", activated, request_id, trace_id)
        return approved

    def bank_change_request(self, scope: ScopeContext, request_id: str) -> BankChangeRequest:
        request = self._bank_requests.get((scope.tenant_id, request_id))
        if request is None:
            raise SupplierScopeError("bank change request is outside the authorized tenant scope")
        return request

    def active_bank_details(self, scope: ScopeContext, supplier_id: str) -> BankDetails | None:
        """Return only bank data that eligible dual control has approved."""
        return self.supplier(scope, supplier_id).active_bank_details

    # Effective-dated contract versions

    def register_contract_version(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        version: ContractVersion,
        trace_id: str,
    ) -> ContractVersion:
        self._authorization.authorize(principal_id, session_id, scope, CONTRACT_WRITE)
        self.supplier(scope, version.supplier_id)
        self._validate_contract_version(version)
        key = (scope.tenant_id, version.version_id)
        if key in self._contract_versions:
            raise SupplierValidationError("contract version identifiers must be unique within a tenant")
        previous = self._latest_registered_version(scope.tenant_id, version.contract_id)
        if previous is not None:
            if version.version_number <= previous.version_number:
                raise SupplierValidationError("contract version numbers must increase")
            if version.effective_from <= previous.effective_from:
                raise SupplierValidationError("contract version effective time must follow the prior version")
        registered = ContractVersion(
            version.version_id,
            scope.tenant_id,
            version.contract_id,
            version.supplier_id,
            version.version_number,
            dict(version.terms),
            version.effective_from,
            version.expires_on,
            "registered",
        )
        self._contract_versions[key] = registered
        self._record_audit(
            principal_id, CONTRACT_WRITE, "supplier.contract.register", trace_id, "registered"
        )
        return registered

    def activate_contract_version(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        version_id: str,
        at: datetime,
        trace_id: str,
    ) -> ContractVersion:
        self._authorization.authorize(principal_id, session_id, scope, CONTRACT_WRITE)
        version = self.contract_version(scope, version_id)
        if version.status == "active":
            return version
        if version.status == "superseded":
            raise SupplierValidationError("superseded contract versions cannot be activated again")
        if at.tzinfo is None:
            raise SupplierValidationError("contract activation time must be timezone-aware")
        if version.effective_from > at:
            raise SupplierValidationError("contract version is not yet effective")
        for key, candidate in tuple(self._contract_versions.items()):
            if (
                candidate.tenant_id == scope.tenant_id
                and candidate.contract_id == version.contract_id
                and candidate.status == "active"
            ):
                self._contract_versions[key] = ContractVersion(
                    candidate.version_id,
                    candidate.tenant_id,
                    candidate.contract_id,
                    candidate.supplier_id,
                    candidate.version_number,
                    candidate.terms,
                    candidate.effective_from,
                    candidate.expires_on,
                    "superseded",
                )
        activated = ContractVersion(
            version.version_id,
            version.tenant_id,
            version.contract_id,
            version.supplier_id,
            version.version_number,
            version.terms,
            version.effective_from,
            version.expires_on,
            "active",
        )
        self._contract_versions[(scope.tenant_id, version_id)] = activated
        record = self.supplier(scope, version.supplier_id)
        self._record_audit(
            principal_id, CONTRACT_WRITE, "supplier.contract.activate", trace_id, "activated"
        )
        self._publish("ContractActivated", record, version_id, trace_id)
        return activated

    def contract_version(self, scope: ScopeContext, version_id: str) -> ContractVersion:
        version = self._contract_versions.get((scope.tenant_id, version_id))
        if version is None:
            raise SupplierScopeError("contract version is outside the authorized tenant scope")
        return version

    def effective_terms(
        self, scope: ScopeContext, contract_id: str, at: datetime
    ) -> Mapping[str, str]:
        """Return the terms applied at a point in time. Prior versions keep their own terms."""
        applicable = [
            version
            for version in self._contract_versions.values()
            if version.tenant_id == scope.tenant_id
            and version.contract_id == contract_id
            and version.status in {"active", "superseded"}
            and version.effective_from <= at
        ]
        if not applicable:
            raise SupplierValidationError("no contract version is effective at the requested time")
        selected = max(applicable, key=lambda version: (version.effective_from, version.version_number))
        return dict(selected.terms)

    # Expiry advisories

    def register_certification(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        certification: Certification,
        trace_id: str,
    ) -> Certification:
        self._authorization.authorize(principal_id, session_id, scope, SUPPLIER_WRITE)
        self.supplier(scope, certification.supplier_id)
        if not certification.certification_id or not certification.kind:
            raise SupplierValidationError("certification identifier and kind are required")
        key = (scope.tenant_id, certification.certification_id)
        if key in self._certifications:
            raise SupplierValidationError("certification identifiers must be unique within a tenant")
        stored = Certification(
            certification.certification_id,
            scope.tenant_id,
            certification.supplier_id,
            certification.kind,
            certification.expires_on,
        )
        self._certifications[key] = stored
        self._record_audit(
            principal_id, SUPPLIER_WRITE, "supplier.certification.register", trace_id, "registered"
        )
        return stored

    def advise_expiries(
        self,
        scope: ScopeContext,
        as_of: date,
        trace_id: str,
        horizon_days: int = DEFAULT_EXPIRY_HORIZON_DAYS,
    ) -> tuple[ExpiryAdvisory, ...]:
        """Notify the responsible scope once for each certification or contract nearing expiry."""
        if horizon_days <= 0:
            raise SupplierValidationError("expiry horizon must be positive")
        horizon = as_of + timedelta(days=horizon_days)
        advisories: list[ExpiryAdvisory] = []
        for certification in sorted(
            (item for item in self._certifications.values() if item.tenant_id == scope.tenant_id),
            key=lambda item: (item.expires_on, item.certification_id),
        ):
            if as_of <= certification.expires_on <= horizon:
                advisory = self._advise(
                    scope,
                    "certification",
                    certification.certification_id,
                    certification.supplier_id,
                    certification.expires_on,
                    trace_id,
                )
                if advisory is not None:
                    advisories.append(advisory)
        for version in sorted(
            (
                item
                for item in self._contract_versions.values()
                if item.tenant_id == scope.tenant_id and item.status == "active"
            ),
            key=lambda item: (item.version_number, item.version_id),
        ):
            if version.expires_on is not None and as_of <= version.expires_on <= horizon:
                advisory = self._advise(
                    scope,
                    "contract",
                    version.version_id,
                    version.supplier_id,
                    version.expires_on,
                    trace_id,
                )
                if advisory is not None:
                    advisories.append(advisory)
        return tuple(advisories)

    # Internals

    def _advise(
        self,
        scope: ScopeContext,
        subject_type: str,
        subject_id: str,
        supplier_id: str,
        expires_on: date,
        trace_id: str,
    ) -> ExpiryAdvisory | None:
        key = (scope.tenant_id, subject_type, subject_id, expires_on)
        if key in self._advised:
            return None
        record = self.supplier(scope, supplier_id)
        self._advised.add(key)
        self._record_audit(
            "supplier-governance", SUPPLIER_WRITE, f"supplier.expiry.{subject_type}", trace_id, "advised"
        )
        self._publish("SupplierExpiryApproaching", record, subject_id, trace_id)
        return ExpiryAdvisory(
            subject_type,
            subject_id,
            supplier_id,
            record.responsible_organization_id,
            expires_on,
        )

    def _latest_registered_version(self, tenant_id: str, contract_id: str) -> ContractVersion | None:
        candidates = [
            version
            for version in self._contract_versions.values()
            if version.tenant_id == tenant_id and version.contract_id == contract_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda version: (version.version_number, version.effective_from))

    def _record_audit(
        self, actor_id: str, policy: str, source: str, trace_id: str, result: str
    ) -> None:
        self._audit.record(
            actor_id,
            "supplier-governance",
            source,
            result,
            policy,
            trace_id,
            result,
        )

    def _publish(
        self, event_type: str, record: SupplierRecord, subject_id: str, trace_id: str
    ) -> None:
        self.outbox.append(
            SupplierEvent(
                event_type,
                record.tenant_id,
                record.supplier_id,
                subject_id,
                record.responsible_organization_id,
                trace_id,
            )
        )

    @staticmethod
    def _validate_supplier(command: SupplierCommand) -> None:
        if not command.supplier_id or not command.name:
            raise SupplierValidationError("supplier identifier and name are required")
        if not command.responsible_organization_id:
            raise SupplierValidationError("a responsible organization is required")
        if command.status not in SUPPLIER_STATUSES:
            raise SupplierValidationError("supplier status must be draft, active, suspended, or inactive")

    @staticmethod
    def _validate_bank_details(details: BankDetails) -> None:
        if not all((details.account_holder, details.account_number, details.bank_identifier)):
            raise SupplierValidationError("bank account holder, number, and identifier are required")

    @staticmethod
    def _validate_contract_version(version: ContractVersion) -> None:
        if not version.version_id or not version.contract_id or not version.supplier_id:
            raise SupplierValidationError("contract, version, and supplier identifiers are required")
        if version.version_number <= 0:
            raise SupplierValidationError("contract version numbers must be positive")
        if not version.terms:
            raise SupplierValidationError("contract versions require configured terms")
        if version.effective_from.tzinfo is None:
            raise SupplierValidationError("contract effective time must be timezone-aware")
        if version.status not in CONTRACT_STATUSES:
            raise SupplierValidationError("contract status must be registered, active, or superseded")


class SupplierAdministrationApi:
    """Typed command boundary for supplier and contract administration."""

    def __init__(self, service: SupplierGovernanceService) -> None:
        self._service = service

    def register_supplier(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        command: SupplierCommand,
        trace_id: str,
    ) -> SupplierRecord:
        return self._service.register_supplier(principal_id, session_id, scope, command, trace_id)

    def request_bank_change(
        self,
        requester_id: str,
        session_id: str,
        scope: ScopeContext,
        supplier_id: str,
        request_id: str,
        proposed: BankDetails,
        idempotency_key: str,
        requested_at: datetime,
        trace_id: str,
    ) -> BankChangeRequest:
        return self._service.request_bank_change(
            requester_id,
            session_id,
            scope,
            supplier_id,
            request_id,
            proposed,
            idempotency_key,
            requested_at,
            trace_id,
        )

    def approve_bank_change(
        self,
        approver_id: str,
        session_id: str,
        scope: ScopeContext,
        request_id: str,
        decided_at: datetime,
        trace_id: str,
    ) -> BankChangeRequest:
        return self._service.approve_bank_change(
            approver_id, session_id, scope, request_id, decided_at, trace_id
        )

    def activate_contract_version(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        version_id: str,
        at: datetime,
        trace_id: str,
    ) -> ContractVersion:
        return self._service.activate_contract_version(
            principal_id, session_id, scope, version_id, at, trace_id
        )
