from foundation.access import AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.organization import ScopeContext


def test_revocation_in_one_tenant_does_not_block_the_same_session_in_another_tenant() -> None:
    revocations = SessionRevocationService()
    service = AuthorizationService(revocations)
    service.grant(PermissionGrant("principal-a", "tenant-a", "payment.submit"))
    service.grant(PermissionGrant("principal-a", "tenant-b", "payment.submit"))
    revocations.revoke("session-a", "tenant-a")

    service.authorize(
        "principal-a",
        "session-a",
        ScopeContext(tenant_id="tenant-b"),
        "payment.submit",
    )

    assert service.outbox[-1].allowed is True
