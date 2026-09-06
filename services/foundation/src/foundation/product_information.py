"""Tenant-scoped Product Information commands and API contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class ProductValidationError(ValueError):
    """Raised when product data does not meet the Product Information contract."""


class DuplicateIdentifierError(ProductValidationError):
    """Raised when an identifier is already assigned within a tenant."""


_LIFECYCLE_STATUSES = frozenset({"draft", "active", "discontinued"})


@dataclass(frozen=True)
class ProductCommand:
    product_id: str
    name: str
    unit_of_measure: str
    lifecycle_status: str
    identifiers: tuple[str, ...] = ()
    variant_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductRecord:
    product_id: str
    tenant_id: str
    name: str
    unit_of_measure: str
    lifecycle_status: str
    identifiers: tuple[str, ...]
    variant_ids: tuple[str, ...]

    @property
    def permits_dependent_operations(self) -> bool:
        return self.lifecycle_status == "active"


@dataclass(frozen=True)
class ProductEvent:
    event_type: str
    product_id: str
    tenant_id: str
    lifecycle_status: str
    trace_id: str


class ProductInformationService:
    """Owns Product Information validation, authorization, audit, and events."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._products: dict[tuple[str, str], ProductRecord] = {}
        self._identifier_owners: dict[tuple[str, str], str] = {}
        self.outbox: list[ProductEvent] = []

    def create(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        command: ProductCommand,
        trace_id: str,
    ) -> ProductRecord:
        self._authorize(principal_id, session_id, scope, "product.write")
        self._validate(command)
        key = (scope.tenant_id, command.product_id)
        if key in self._products:
            raise ProductValidationError("product identifiers must be unique within a tenant")
        self._ensure_identifiers_available(scope.tenant_id, command.identifiers)
        record = self._record(scope.tenant_id, command)
        self._products[key] = record
        self._reserve_identifiers(record)
        self._record_audit(principal_id, trace_id, "created")
        self._publish(record, "ProductChanged", trace_id)
        return record

    def change_lifecycle(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        product_id: str,
        lifecycle_status: str,
        trace_id: str,
    ) -> ProductRecord:
        self._authorize(principal_id, session_id, scope, "product.write")
        if lifecycle_status not in _LIFECYCLE_STATUSES:
            raise ProductValidationError("lifecycle status must be draft, active, or discontinued")
        key = (scope.tenant_id, product_id)
        current = self._products.get(key)
        if current is None:
            raise ProductValidationError("product is outside the tenant scope")
        updated = ProductRecord(
            current.product_id,
            current.tenant_id,
            current.name,
            current.unit_of_measure,
            lifecycle_status,
            current.identifiers,
            current.variant_ids,
        )
        self._products[key] = updated
        self._record_audit(principal_id, trace_id, "lifecycle-changed")
        self._publish(updated, "ProductLifecycleChanged", trace_id)
        return updated

    def import_products(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        commands: tuple[ProductCommand, ...],
        trace_id: str,
    ) -> tuple[ProductRecord, ...]:
        self._authorize(principal_id, session_id, scope, "product.write")
        pending_ids: set[str] = set()
        pending_products: set[str] = set()
        for command in commands:
            self._validate(command)
            if command.product_id in pending_products or (scope.tenant_id, command.product_id) in self._products:
                raise ProductValidationError("product identifiers must be unique within a tenant")
            pending_products.add(command.product_id)
            for identifier in command.identifiers:
                if identifier in pending_ids or (scope.tenant_id, identifier) in self._identifier_owners:
                    raise DuplicateIdentifierError("identifier is already assigned within the tenant")
                pending_ids.add(identifier)
        records = tuple(self._record(scope.tenant_id, command) for command in commands)
        for record in records:
            self._products[(scope.tenant_id, record.product_id)] = record
            self._reserve_identifiers(record)
            self._publish(record, "ProductChanged", trace_id)
        self._record_audit(principal_id, trace_id, "imported")
        return records

    def get(self, scope: ScopeContext, product_id: str) -> ProductRecord:
        record = self._products.get((scope.tenant_id, product_id))
        if record is None:
            raise ProductValidationError("product is outside the tenant scope")
        return record

    def _authorize(self, principal_id: str, session_id: str, scope: ScopeContext, action: str) -> None:
        self._authorization.authorize(principal_id, session_id, scope, action)

    @staticmethod
    def _validate(command: ProductCommand) -> None:
        if not command.product_id or not command.name:
            raise ProductValidationError("product id and name are required")
        if not command.unit_of_measure:
            raise ProductValidationError("unit of measure is required")
        if command.lifecycle_status not in _LIFECYCLE_STATUSES:
            raise ProductValidationError("lifecycle status must be draft, active, or discontinued")
        if any(not identifier for identifier in command.identifiers):
            raise ProductValidationError("identifiers must be non-empty")
        if len(set(command.identifiers)) != len(command.identifiers):
            raise DuplicateIdentifierError("identifiers must be unique per product")
        if any(not variant_id for variant_id in command.variant_ids):
            raise ProductValidationError("variant identifiers must be non-empty")
        if len(set(command.variant_ids)) != len(command.variant_ids):
            raise ProductValidationError("variant identifiers must be unique per product")

    def _ensure_identifiers_available(self, tenant_id: str, identifiers: tuple[str, ...]) -> None:
        for identifier in identifiers:
            if (tenant_id, identifier) in self._identifier_owners:
                raise DuplicateIdentifierError("identifier is already assigned within the tenant")

    @staticmethod
    def _record(tenant_id: str, command: ProductCommand) -> ProductRecord:
        return ProductRecord(
            command.product_id,
            tenant_id,
            command.name,
            command.unit_of_measure,
            command.lifecycle_status,
            command.identifiers,
            command.variant_ids,
        )

    def _reserve_identifiers(self, record: ProductRecord) -> None:
        for identifier in record.identifiers:
            self._identifier_owners[(record.tenant_id, identifier)] = record.product_id

    def _record_audit(self, principal_id: str, trace_id: str, result: str) -> None:
        self._audit.record(principal_id, "product-administration", "product", result, "product.write", trace_id, result)

    def _publish(self, record: ProductRecord, event_type: str, trace_id: str) -> None:
        self.outbox.append(ProductEvent(event_type, record.product_id, record.tenant_id, record.lifecycle_status, trace_id))


class ProductAdministrationApi:
    """Typed command boundary for Product Information administration."""

    def __init__(self, service: ProductInformationService) -> None:
        self._service = service

    def create_product(self, principal_id: str, session_id: str, scope: ScopeContext, command: ProductCommand, trace_id: str) -> ProductRecord:
        return self._service.create(principal_id, session_id, scope, command, trace_id)

    def import_products(self, principal_id: str, session_id: str, scope: ScopeContext, commands: tuple[ProductCommand, ...], trace_id: str) -> tuple[ProductRecord, ...]:
        return self._service.import_products(principal_id, session_id, scope, commands, trace_id)

    def change_lifecycle(self, principal_id: str, session_id: str, scope: ScopeContext, product_id: str, lifecycle_status: str, trace_id: str) -> ProductRecord:
        return self._service.change_lifecycle(principal_id, session_id, scope, product_id, lifecycle_status, trace_id)
