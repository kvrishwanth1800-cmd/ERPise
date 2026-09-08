from __future__ import annotations

import dataclasses
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
from foundation.organization import ScopeContext
from foundation.payment import (
    PaymentIntent,
    PaymentOrchestrationService,
    PaymentRefund,
    PaymentTransaction,
    PaymentValidationError,
    SettlementException,
)
from foundation.webhook_verifier import ProviderCallback, ProviderWebhookVerifier

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
SECRET = "local-fake-provider-secret"


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id, is_tenant_administrator=True)


def service() -> PaymentOrchestrationService:
    access = AuthorizationService(SessionRevocationService())
    access.grant(PermissionGrant("agent", "tenant-a", "payment.write"))
    return PaymentOrchestrationService(access, AuditRecorder(), ProviderWebhookVerifier(SECRET))


def intent(subject: PaymentOrchestrationService, intent_id: str = "intent-1") -> PaymentIntent:
    return subject.create_intent(
        "agent",
        "session",
        scope(),
        PaymentIntent(intent_id, "tenant-a", f"order-{intent_id}", Decimal("25.00"), "USD", NOW),
        f"trace-{intent_id}",
    )


def signed_callback(
    verifier: ProviderWebhookVerifier,
    kind: str,
    subject_id: str,
    amount_cents: int,
    tenant_id: str = "tenant-a",
) -> ProviderCallback:
    unsigned = ProviderCallback("cb", tenant_id, kind, subject_id, "prov-ref", amount_cents, "")
    return ProviderCallback(
        callback_id="cb",
        tenant_id=tenant_id,
        kind=kind,
        subject_id=subject_id,
        provider_reference="prov-ref",
        amount_cents=amount_cents,
        signature=ProviderWebhookVerifier(SECRET).sign(unsigned),
    )


def test_capture_is_idempotent_by_key_and_requires_a_verified_callback() -> None:
    subject = service()
    intent(subject)
    callback = signed_callback(subject._webhook_verifier, "capture", "intent-1", 1000)
    transaction = PaymentTransaction(
        "txn-1", "tenant-a", "intent-1", "idem-1", Decimal("10.00"), "prov-ref", NOW
    )

    first = subject.capture("agent", "session", scope(), callback, transaction, "trace")
    retry = subject.capture(
        "agent",
        "session",
        scope(),
        callback,
        PaymentTransaction(
            "txn-2", "tenant-a", "intent-1", "idem-1", Decimal("10.00"), "prov-ref", NOW
        ),
        "trace-retry",
    )
    assert first == retry
    assert subject.transactions_for_intent(scope(), "intent-1") == (first,)


def test_capture_rejects_an_unverified_or_mismatched_callback() -> None:
    subject = service()
    intent(subject)
    bad_signature = ProviderCallback(
        "cb", "tenant-a", "capture", "intent-1", "prov-ref", 1000, "not-a-real-signature"
    )
    transaction = PaymentTransaction(
        "txn-1", "tenant-a", "intent-1", "idem-1", Decimal("10.00"), "prov-ref", NOW
    )
    with pytest.raises(PaymentValidationError, match="verified provider callback"):
        subject.capture("agent", "session", scope(), bad_signature, transaction, "trace")


def test_refund_cannot_exceed_captured_amount_and_is_idempotent() -> None:
    subject = service()
    intent(subject)
    capture_callback = signed_callback(subject._webhook_verifier, "capture", "intent-1", 2000)
    transaction = subject.capture(
        "agent",
        "session",
        scope(),
        capture_callback,
        PaymentTransaction(
            "txn-1", "tenant-a", "intent-1", "idem-1", Decimal("20.00"), "prov-ref", NOW
        ),
        "trace-capture",
    )
    refund_callback = signed_callback(subject._webhook_verifier, "refund", "txn-1", 2500)
    with pytest.raises(PaymentValidationError, match="remaining captured amount"):
        subject.refund(
            "agent",
            "session",
            scope(),
            refund_callback,
            PaymentRefund(
                "refund-1", "tenant-a", "txn-1", "idem-r1", Decimal("25.00"), "customer request", NOW
            ),
            "trace-refund",
        )

    ok_callback = signed_callback(subject._webhook_verifier, "refund", "txn-1", 500)
    first = subject.refund(
        "agent",
        "session",
        scope(),
        ok_callback,
        PaymentRefund(
            "refund-2", "tenant-a", "txn-1", "idem-r2", Decimal("5.00"), "customer request", NOW
        ),
        "trace-refund-2",
    )
    retry = subject.refund(
        "agent",
        "session",
        scope(),
        ok_callback,
        PaymentRefund(
            "refund-3", "tenant-a", "txn-1", "idem-r2", Decimal("5.00"), "customer request", NOW
        ),
        "trace-refund-3",
    )
    assert first == retry
    assert transaction.transaction_id == "txn-1"


def test_settlement_exception_is_idempotent_and_immutable() -> None:
    subject = service()
    intent(subject)
    capture_callback = signed_callback(subject._webhook_verifier, "capture", "intent-1", 1000)
    subject.capture(
        "agent",
        "session",
        scope(),
        capture_callback,
        PaymentTransaction(
            "txn-1", "tenant-a", "intent-1", "idem-1", Decimal("10.00"), "prov-ref", NOW
        ),
        "trace-capture",
    )
    exception = SettlementException(
        "exc-1", "tenant-a", "txn-1", Decimal("10.00"), Decimal("9.50"), "provider fee mismatch", NOW
    )
    first = subject.report_settlement_exception("agent", "session", scope(), exception, "trace")
    retry = subject.report_settlement_exception("agent", "session", scope(), exception, "trace-2")
    assert first == retry
    with pytest.raises(PaymentValidationError, match="immutable"):
        subject.report_settlement_exception(
            "agent",
            "session",
            scope(),
            SettlementException(
                "exc-1", "tenant-a", "txn-1", Decimal("10.00"), Decimal("8.00"), "different", NOW
            ),
            "trace-3",
        )


def test_tenant_scope_and_authorization_are_denied() -> None:
    subject = service()
    intent(subject)
    with pytest.raises(PaymentValidationError, match="tenant scope"):
        subject.transactions_for_intent(scope("tenant-b"), "intent-1")
    with pytest.raises(AuthorizationDeniedError):
        subject.create_intent(
            "other",
            "session",
            scope(),
            PaymentIntent("intent-2", "tenant-a", "order-2", Decimal("5.00"), "USD", NOW),
            "trace",
        )


@pytest.mark.parametrize(
    "payment_class",
    (PaymentIntent, PaymentTransaction, PaymentRefund, SettlementException),
)
def test_payment_dataclasses_never_carry_cardholder_data_fields(payment_class: type) -> None:
    forbidden = ("pan", "card", "cvv")
    field_names = {field.name.lower() for field in dataclasses.fields(payment_class)}
    assert not any(term in name for name in field_names for term in forbidden)
