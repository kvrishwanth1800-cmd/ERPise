import pytest

from foundation.organization import (
    OrganizationHierarchyService,
    ProtectedDeletionError,
    ScopeContext,
    ScopeDeniedError,
    ScopeResolver,
)


def tenant_admin(tenant_id: str) -> ScopeContext:
    return ScopeResolver().resolve(tenant_id, tenant_id, is_tenant_administrator=True)


def test_hierarchy_records_retain_tenant_and_parent_scope() -> None:
    service = OrganizationHierarchyService()
    scope = tenant_admin("tenant-a")
    root = service.create(scope, "root", None)
    child = service.create(scope, "store", root.organization_id)

    assert child.tenant_id == "tenant-a"
    assert child.parent_organization_id == "root"
    assert service.outbox[-1].action == "created"


def test_cross_tenant_reads_and_writes_are_denied() -> None:
    service = OrganizationHierarchyService()
    service.create(tenant_admin("tenant-a"), "root", None)
    other_scope = tenant_admin("tenant-b")

    with pytest.raises(ScopeDeniedError):
        service.get(other_scope, "root")
    with pytest.raises(ScopeDeniedError):
        service.update_settings(other_scope, "root", {"currency": "USD"})


def test_effective_settings_resolve_from_root_to_child() -> None:
    service = OrganizationHierarchyService()
    scope = tenant_admin("tenant-a")
    service.create(scope, "root", None, {"currency": "USD", "timezone": "UTC"})
    service.create(scope, "store", "root", {"timezone": "America/New_York"})

    assert service.effective_settings(scope, "store") == {
        "currency": "USD",
        "timezone": "America/New_York",
    }


def test_dependent_operations_protect_organization_deletion() -> None:
    service = OrganizationHierarchyService()
    scope = tenant_admin("tenant-a")
    service.create(scope, "root", None)
    service.register_dependent_operation("root")

    with pytest.raises(ProtectedDeletionError):
        service.delete(scope, "root")
