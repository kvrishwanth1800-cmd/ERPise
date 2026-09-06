from __future__ import annotations

import pytest

from foundation.access import AuthorizationDeniedError, AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext
from foundation.product_information import DuplicateIdentifierError, ProductCommand, ProductInformationService, ProductValidationError


def service() -> ProductInformationService:
    revocations = SessionRevocationService()
    authorization = AuthorizationService(revocations)
    authorization.grant(PermissionGrant("owner", "tenant-a", "product.write"))
    return ProductInformationService(authorization, AuditRecorder())


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def product(product_id: str = "product-1", identifier: str = "barcode-1") -> ProductCommand:
    return ProductCommand(product_id, "Tea", "each", "draft", (identifier,), ("variant-1",))


def test_requires_uom_and_lifecycle() -> None:
    subject = service()
    with pytest.raises(ProductValidationError, match="unit of measure"):
        subject.create("owner", "session", scope(), ProductCommand("id", "Tea", "", "draft"), "trace")
    with pytest.raises(ProductValidationError, match="lifecycle status"):
        subject.create("owner", "session", scope(), ProductCommand("id", "Tea", "each", "obsolete"), "trace")


def test_identifier_is_unique_within_tenant_only() -> None:
    subject = service()
    subject.create("owner", "session", scope(), product(), "trace-1")
    with pytest.raises(DuplicateIdentifierError, match="within the tenant"):
        subject.create("owner", "session", scope(), product("product-2"), "trace-2")


def test_lifecycle_exposes_dependent_operation_restriction_and_event() -> None:
    subject = service()
    created = subject.create("owner", "session", scope(), product(), "trace-1")
    assert not created.permits_dependent_operations
    active = subject.change_lifecycle("owner", "session", scope(), "product-1", "active", "trace-2")
    assert active.permits_dependent_operations
    assert subject.outbox[-1].event_type == "ProductLifecycleChanged"


def test_failed_import_is_atomic() -> None:
    subject = service()
    with pytest.raises(DuplicateIdentifierError):
        subject.import_products(
            "owner",
            "session",
            scope(),
            (product("product-1", "shared"), product("product-2", "shared")),
            "trace",
        )
    with pytest.raises(ProductValidationError, match="outside"):
        subject.get(scope(), "product-1")
    assert subject.outbox == []


def test_deny_by_default_and_tenant_isolation() -> None:
    subject = service()
    with pytest.raises(AuthorizationDeniedError):
        subject.create("other", "session", scope(), product(), "trace")
    subject.create("owner", "session", scope(), product(), "trace")
    with pytest.raises(ProductValidationError, match="outside"):
        subject.get(scope("tenant-b"), "product-1")
