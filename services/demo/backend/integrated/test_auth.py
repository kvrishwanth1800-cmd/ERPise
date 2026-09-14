# ruff: noqa: I001
"""Focused authorization coverage for the governed demonstration boundary."""

from unittest import TestCase

from foundation.access import AuthorizationDeniedError
from foundation.organization import ScopeDeniedError
from integrated.auth import FoundationSessionAuthorizer
from integrated.config import IDENTITY


def demo_session() -> dict[str, str]:
    """Return the trusted demo identity as an authenticated session."""
    return {
        "session_id": "session-1",
        "tenant_id": IDENTITY.tenant_id,
        "user_id": IDENTITY.user_id,
        "role": IDENTITY.role,
    }


class FoundationSessionAuthorizerTest(TestCase):
    """Verifies authorization decisions at the governed runtime boundary."""

    def test_authorizes_granted_tenant_scoped_action(self) -> None:
        authorizer = FoundationSessionAuthorizer()

        scope = authorizer.authorize(demo_session(), "orders.write")

        self.assertEqual(scope.tenant_id, IDENTITY.tenant_id)
        self.assertTrue(scope.is_tenant_administrator)

    def test_denies_cross_tenant_session(self) -> None:
        authorizer = FoundationSessionAuthorizer()
        session = demo_session()
        session["tenant_id"] = "other-tenant"

        with self.assertRaises(ScopeDeniedError):
            authorizer.authorize(session, "orders.read")

    def test_denies_unrecognized_role(self) -> None:
        authorizer = FoundationSessionAuthorizer()
        session = demo_session()
        session["role"] = "viewer"

        with self.assertRaises(AuthorizationDeniedError):
            authorizer.authorize(session, "orders.read")

    def test_denies_session_after_revocation(self) -> None:
        authorizer = FoundationSessionAuthorizer()
        session = demo_session()

        authorizer.revoke(session)

        with self.assertRaises(AuthorizationDeniedError):
            authorizer.authorize(session, "orders.read")
