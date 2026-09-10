"""Foundation authorization adapter for legacy demo HTTP sessions."""

from __future__ import annotations

from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.organization import ScopeContext, ScopeResolver

from integrated.config import IDENTITY


class FoundationSessionAuthorizer:
    """Resolves trusted tenant scope and delegates decisions to Foundation access."""

    def __init__(self) -> None:
        self.revocations = SessionRevocationService()
        self.authorization = AuthorizationService(self.revocations)
        self.scopes = ScopeResolver()
        for action in {
            "session.read",
            "products.read",
            "reports.read",
            "orders.read",
            "consent.write",
            "orders.write",
            "cases.write",
        }:
            self.authorization.grant(
                PermissionGrant(
                    principal_id=IDENTITY.user_id,
                    tenant_id=IDENTITY.tenant_id,
                    action=action,
                    organization_ids=frozenset({IDENTITY.organization_id}),
                )
            )

    def scope_for(self, session: dict[str, str]) -> ScopeContext:
        if session["role"] != IDENTITY.role:
            raise AuthorizationDeniedError("The session role is not permitted for the demo.")
        return self.scopes.resolve(
            authenticated_tenant_id=session["tenant_id"],
            requested_tenant_id=IDENTITY.tenant_id,
            authorized_organization_ids=frozenset({IDENTITY.organization_id}),
            is_tenant_administrator=True,
        )

    def authorize(self, session: dict[str, str], action: str) -> ScopeContext:
        scope = self.scope_for(session)
        self.authorization.authorize(
            principal_id=session["user_id"],
            session_id=session["session_id"],
            scope=scope,
            action=action,
            organization_id=IDENTITY.organization_id,
        )
        return scope

    def revoke(self, session: dict[str, str]) -> None:
        self.revocations.revoke(session["session_id"], session["tenant_id"])
