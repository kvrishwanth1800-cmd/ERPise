"""Tenant-scoped organization hierarchy primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class ScopeDeniedError(PermissionError):
    """Raised when a caller acts outside its server-resolved scope."""


class ProtectedDeletionError(ValueError):
    """Raised when an organization has dependent operational records."""


class HierarchyValidationError(ValueError):
    """Raised when a hierarchy command is invalid."""


@dataclass(frozen=True)
class ScopeContext:
    tenant_id: str
    authorized_organization_ids: frozenset[str] = frozenset()
    is_tenant_administrator: bool = False


class ScopeResolver:
    """Creates trusted scope contexts from authenticated tenant claims."""

    def resolve(
        self,
        authenticated_tenant_id: str,
        requested_tenant_id: str,
        authorized_organization_ids: frozenset[str] = frozenset(),
        is_tenant_administrator: bool = False,
    ) -> ScopeContext:
        if not authenticated_tenant_id or authenticated_tenant_id != requested_tenant_id:
            raise ScopeDeniedError("The requested tenant is outside the authenticated scope.")
        return ScopeContext(
            tenant_id=authenticated_tenant_id,
            authorized_organization_ids=authorized_organization_ids,
            is_tenant_administrator=is_tenant_administrator,
        )


@dataclass(frozen=True)
class OrganizationRecord:
    organization_id: str
    tenant_id: str
    parent_organization_id: str | None
    settings: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OrganizationChanged:
    organization_id: str
    tenant_id: str
    action: str


class OrganizationHierarchyService:
    """Owns tenant-scoped hierarchy records and deterministic settings resolution."""

    def __init__(self) -> None:
        self._records: dict[str, OrganizationRecord] = {}
        self._dependent_operation_counts: dict[str, int] = {}
        self.outbox: list[OrganizationChanged] = []

    def create(
        self,
        scope: ScopeContext,
        organization_id: str,
        parent_organization_id: str | None,
        settings: Mapping[str, str] | None = None,
    ) -> OrganizationRecord:
        if not organization_id or organization_id in self._records:
            raise HierarchyValidationError("Organization identifiers must be unique and non-empty.")
        self._authorize_parent(scope, parent_organization_id)
        record = OrganizationRecord(
            organization_id=organization_id,
            tenant_id=scope.tenant_id,
            parent_organization_id=parent_organization_id,
            settings=dict(settings or {}),
        )
        self._records[organization_id] = record
        self._publish(record, "created")
        return record

    def update_settings(
        self, scope: ScopeContext, organization_id: str, settings: Mapping[str, str]
    ) -> OrganizationRecord:
        record = self.get(scope, organization_id)
        updated = OrganizationRecord(
            organization_id=record.organization_id,
            tenant_id=record.tenant_id,
            parent_organization_id=record.parent_organization_id,
            settings=dict(settings),
        )
        self._records[organization_id] = updated
        self._publish(updated, "updated")
        return updated

    def get(self, scope: ScopeContext, organization_id: str) -> OrganizationRecord:
        record = self._records.get(organization_id)
        if record is None or record.tenant_id != scope.tenant_id:
            raise ScopeDeniedError("The organization is outside the authorized tenant scope.")
        if not scope.is_tenant_administrator and organization_id not in scope.authorized_organization_ids:
            raise ScopeDeniedError("The organization is outside the authorized organization scope.")
        return record

    def effective_settings(self, scope: ScopeContext, organization_id: str) -> dict[str, str]:
        record = self.get(scope, organization_id)
        lineage: list[OrganizationRecord] = []
        while True:
            lineage.append(record)
            if record.parent_organization_id is None:
                break
            parent = self._records.get(record.parent_organization_id)
            if parent is None or parent.tenant_id != scope.tenant_id:
                raise HierarchyValidationError("Hierarchy parent is invalid for the tenant.")
            record = parent
        resolved: dict[str, str] = {}
        for ancestor in reversed(lineage):
            resolved.update(ancestor.settings)
        return resolved

    def register_dependent_operation(self, organization_id: str) -> None:
        if organization_id not in self._records:
            raise HierarchyValidationError("Cannot register a dependency for an unknown organization.")
        self._dependent_operation_counts[organization_id] = (
            self._dependent_operation_counts.get(organization_id, 0) + 1
        )

    def delete(self, scope: ScopeContext, organization_id: str) -> None:
        record = self.get(scope, organization_id)
        if self._dependent_operation_counts.get(organization_id, 0) > 0:
            raise ProtectedDeletionError("Organizations with dependent operations cannot be deleted.")
        if any(item.parent_organization_id == organization_id for item in self._records.values()):
            raise ProtectedDeletionError("Organizations with child organizations cannot be deleted.")
        del self._records[organization_id]
        self._dependent_operation_counts.pop(organization_id, None)
        self._publish(record, "deleted")

    def _authorize_parent(self, scope: ScopeContext, parent_organization_id: str | None) -> None:
        if parent_organization_id is None:
            if not scope.is_tenant_administrator:
                raise ScopeDeniedError("Only a tenant administrator can create a root organization.")
            return
        self.get(scope, parent_organization_id)

    def _publish(self, record: OrganizationRecord, action: str) -> None:
        self.outbox.append(
            OrganizationChanged(
                organization_id=record.organization_id,
                tenant_id=record.tenant_id,
                action=action,
            )
        )
