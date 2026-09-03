"""Deny-by-default authorization and session revocation primitives."""

from __future__ import annotations

from dataclasses import dataclass, field

from foundation.organization import ScopeContext


class AuthorizationDeniedError(PermissionError):
    """Raised when no applicable permission authorizes a request."""


class ConflictingDutyError(ValueError):
    """Raised when a principal receives incompatible duties."""


@dataclass(frozen=True)
class PermissionGrant:
    principal_id: str
    tenant_id: str
    action: str
    organization_ids: frozenset[str] = frozenset()
    record_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AccessDecisionRecorded:
    principal_id: str
    action: str
    tenant_id: str
    allowed: bool


@dataclass(frozen=True)
class SessionRevoked:
    session_id: str
    tenant_id: str


class SessionRevocationService:
    """Tracks session and credential revocation state for authorization checks."""

    def __init__(self) -> None:
        self._revoked_session_ids: set[str] = set()
        self.outbox: list[SessionRevoked] = []

    def revoke(self, session_id: str, tenant_id: str) -> None:
        if not session_id or not tenant_id:
            raise ValueError("Session and tenant identifiers must be non-empty.")
        self._revoked_session_ids.add(session_id)
        self.outbox.append(SessionRevoked(session_id=session_id, tenant_id=tenant_id))

    def is_revoked(self, session_id: str) -> bool:
        return session_id in self._revoked_session_ids


class AuthorizationService:
    """Evaluates explicit scoped grants and rejects conflicting duty assignments."""

    _conflicting_duties = frozenset({"requester", "approver", "payer", "bank-editor", "reconciler"})

    def __init__(self, session_revocations: SessionRevocationService) -> None:
        self._session_revocations = session_revocations
        self._grants: list[PermissionGrant] = []
        self._duties_by_principal: dict[str, set[str]] = {}
        self.outbox: list[AccessDecisionRecorded] = []

    def grant(self, grant: PermissionGrant) -> None:
        if not grant.principal_id or not grant.tenant_id or not grant.action:
            raise ValueError("Permission grants require principal, tenant, and action identifiers.")
        self._grants.append(grant)

    def assign_duty(self, principal_id: str, duty: str) -> None:
        if not principal_id or not duty:
            raise ValueError("Duty assignments require principal and duty identifiers.")
        current_duties = self._duties_by_principal.setdefault(principal_id, set())
        if duty in self._conflicting_duties and current_duties & self._conflicting_duties:
            raise ConflictingDutyError("A principal cannot hold more than one conflicting duty.")
        current_duties.add(duty)

    def authorize(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        action: str,
        organization_id: str | None = None,
        record_id: str | None = None,
    ) -> None:
        allowed = False
        if not self._session_revocations.is_revoked(session_id):
            allowed = any(
                self._grant_applies(grant, principal_id, scope, action, organization_id, record_id)
                for grant in self._grants
            )
        self.outbox.append(
            AccessDecisionRecorded(
                principal_id=principal_id,
                action=action,
                tenant_id=scope.tenant_id,
                allowed=allowed,
            )
        )
        if not allowed:
            raise AuthorizationDeniedError("The principal lacks an applicable permission.")

    @staticmethod
    def _grant_applies(
        grant: PermissionGrant,
        principal_id: str,
        scope: ScopeContext,
        action: str,
        organization_id: str | None,
        record_id: str | None,
    ) -> bool:
        if grant.principal_id != principal_id or grant.tenant_id != scope.tenant_id:
            return False
        if grant.action != action:
            return False
        if organization_id is not None:
            if organization_id not in scope.authorized_organization_ids and not scope.is_tenant_administrator:
                return False
            if grant.organization_ids and organization_id not in grant.organization_ids:
                return False
        if record_id is not None and grant.record_ids and record_id not in grant.record_ids:
            return False
        return True
