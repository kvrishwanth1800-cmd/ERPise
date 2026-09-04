import pytest
from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    ConflictingDutyError,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.organization import ScopeContext


def organization_scope() -> ScopeContext:
    return ScopeContext(
        tenant_id="tenant-a",
        authorized_organization_ids=frozenset({"store-a"}),
    )


def test_authorization_denies_without_an_explicit_applicable_permission() -> None:
    service = AuthorizationService(SessionRevocationService())

    with pytest.raises(AuthorizationDeniedError):
        service.authorize("principal-a", "session-a", organization_scope(), "purchase.approve")

    assert service.outbox[-1].allowed is False


def test_revoked_session_cannot_authorize_future_work() -> None:
    revocations = SessionRevocationService()
    service = AuthorizationService(revocations)
    service.grant(PermissionGrant("principal-a", "tenant-a", "purchase.approve"))
    revocations.revoke("session-a", "tenant-a")

    with pytest.raises(AuthorizationDeniedError):
        service.authorize("principal-a", "session-a", organization_scope(), "purchase.approve")

    assert revocations.outbox[-1].session_id == "session-a"


def test_conflicting_duties_are_rejected() -> None:
    service = AuthorizationService(SessionRevocationService())
    service.assign_duty("principal-a", "requester")

    with pytest.raises(ConflictingDutyError):
        service.assign_duty("principal-a", "approver")


def test_authorization_enforces_tenant_organization_and_record_scope() -> None:
    service = AuthorizationService(SessionRevocationService())
    service.grant(
        PermissionGrant(
            principal_id="principal-a",
            tenant_id="tenant-a",
            action="payment.submit",
            organization_ids=frozenset({"store-a"}),
            record_ids=frozenset({"payment-1"}),
        )
    )

    service.authorize(
        "principal-a",
        "session-a",
        organization_scope(),
        "payment.submit",
        organization_id="store-a",
        record_id="payment-1",
    )

    with pytest.raises(AuthorizationDeniedError):
        service.authorize(
            "principal-a",
            "session-a",
            organization_scope(),
            "payment.submit",
            organization_id="store-a",
            record_id="payment-2",
        )
