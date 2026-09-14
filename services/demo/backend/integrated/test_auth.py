"""Focused authorization coverage for the governed demonstration boundary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_authorizes_granted_tenant_scoped_action() -> None:
    authorizer = FoundationSessionAuthorizer()

    scope = authorizer.authorize(demo_session(), "orders.write")

    assert scope.tenant_id == IDENTITY.tenant_id
    assert scope.is_tenant_administrator is True


def test_denies_cross_tenant_session() -> None:
    authorizer = FoundationSessionAuthorizer()
    session = demo_session()
    session["tenant_id"] = "other-tenant"

    with pytest.raises(ScopeDeniedError):
        authorizer.authorize(session, "orders.read")


def test_denies_unrecognized_role() -> None:
    authorizer = FoundationSessionAuthorizer()
    session = demo_session()
    session["role"] = "viewer"

    with pytest.raises(AuthorizationDeniedError):
        authorizer.authorize(session, "orders.read")


def test_denies_session_after_revocation() -> None:
    authorizer = FoundationSessionAuthorizer()
    session = demo_session()

    authorizer.revoke(session)

    with pytest.raises(AuthorizationDeniedError):
        authorizer.authorize(session, "orders.read")
