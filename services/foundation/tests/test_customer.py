from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.audit import AuditRecorder
from foundation.customer import (
    ConsentFact,
    CustomerConsentService,
    CustomerValidationError,
    PrivacyRequest,
    StoredValueEffect,
)
from foundation.organization import ScopeContext

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id, is_tenant_administrator=True)


def service() -> CustomerConsentService:
    access = AuthorizationService(SessionRevocationService())
    for action in ("customer.write", "consent.write", "privacy.write", "stored_value.write"):
        access.grant(PermissionGrant("agent", "tenant-a", action))
    return CustomerConsentService(access, AuditRecorder())


def customer(subject: CustomerConsentService, customer_id: str) -> None:
    subject.create_customer("agent", "session", scope(), customer_id, f"customer-{customer_id}")


def test_consent_retains_purpose_evidence_time_scope_and_withdrawal_controls_access() -> None:
    subject = service()
    customer(subject, "c1")
    subject.record_consent(
        "agent",
        "session",
        scope(),
        ConsentFact("grant", "c1", "marketing", "granted", "v2", "signed-form", "email", NOW),
        "trace-1",
    )
    assert subject.has_consent(scope(), "c1", "marketing")
    subject.record_consent(
        "agent",
        "session",
        scope(),
        ConsentFact(
            "withdraw",
            "c1",
            "marketing",
            "withdrawn",
            "v2",
            "customer-request",
            "email",
            NOW.replace(minute=1),
            "grant",
        ),
        "trace-2",
    )
    assert not subject.has_consent(scope(), "c1", "marketing")


def test_identity_history_preserves_merge_and_unmerge() -> None:
    subject = service()
    customer(subject, "one")
    customer(subject, "two")
    result = subject.merge("agent", "session", scope(), "merge-1", "one", "two", NOW, "trace")
    assert result.action == "merged"
    result = subject.unmerge("agent", "session", scope(), "unmerge-1", "one", "two", NOW, "trace")
    assert result.action == "unmerged"


def test_value_correction_is_immutable_linked_reversal_and_is_idempotent() -> None:
    subject = service()
    customer(subject, "c1")
    effect = StoredValueEffect("credit-1", "c1", "store-credit", Decimal("12.50"), "goodwill", NOW)
    subject.post_value("agent", "session", scope(), effect, "trace")
    assert subject.post_value("agent", "session", scope(), effect, "trace") == effect
    subject.reverse_value(
        "agent", "session", scope(), "credit-1", "reverse-1", "correction", NOW, "trace"
    )
    assert subject.balance(scope(), "c1", "store-credit") == Decimal()
    with pytest.raises(CustomerValidationError, match="immutable"):
        subject.post_value(
            "agent",
            "session",
            scope(),
            StoredValueEffect("credit-1", "c1", "store-credit", Decimal("4"), "changed", NOW),
            "trace",
        )


def test_privacy_outcomes_are_scoped_and_retain_legal_evidence() -> None:
    subject = service()
    customer(subject, "c1")
    request = PrivacyRequest("export-1", "c1", "export", "profile-and-consent", "verified-request")
    completed = subject.complete_privacy_request(
        "agent", "session", scope(), request, "exported", "trace"
    )
    assert completed.outcome == "exported"
    assert completed.legal_evidence == "verified-request"


def test_tenant_scope_and_authorization_are_denied() -> None:
    subject = service()
    customer(subject, "c1")
    with pytest.raises(CustomerValidationError, match="tenant scope"):
        subject.has_consent(scope("tenant-b"), "c1", "marketing")
    with pytest.raises(AuthorizationDeniedError):
        subject.create_customer("other", "session", scope(), "c2", "trace")
