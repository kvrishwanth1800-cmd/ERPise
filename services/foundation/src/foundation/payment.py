"""Tenant-scoped payment orchestration: intents, captures, refunds, settlement.

Payment records never carry cardholder data (PAN or CVV): only tokenized
provider references and computed amounts are held or logged. Capture and
refund state changes require a verified provider callback (see
foundation.webhook_verifier); unverified evidence never advances payment
state (ADR-001: verified-evidence-controls-state).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext
from foundation.webhook_verifier import ProviderCallback, ProviderWebhookVerifier


class PaymentValidationError(ValueError):
    """Raised when a payment command cannot preserve the required evidence."""


@dataclass(frozen=True)
class PaymentIntent:
    intent_id: str
    tenant_id: str
    order_reference: str
    amount: Decimal
    currency: str
    created_at: datetime


@dataclass(frozen=True)
class PaymentTransaction:
    transaction_id: str
    tenant_id: str
    intent_id: str
    idempotency_key: str
    amount: Decimal
    provider_reference: str
    captured_at: datetime


@dataclass(frozen=True)
class PaymentRefund:
    refund_id: str
    tenant_id: str
    transaction_id: str
    idempotency_key: str
    amount: Decimal
    reason: str
    refunded_at: datetime


@dataclass(frozen=True)
class SettlementException:
    exception_id: str
    tenant_id: str
    transaction_id: str
    expected_amount: Decimal
    settled_amount: Decimal
    reason: str
    reported_at: datetime


@dataclass(frozen=True)
class PaymentEvent:
    event_type: str
    tenant_id: str
    subject_id: str


class PaymentOrchestrationService:
    """Runs the local-fake payment state machine: intents, captures, refunds."""

    def __init__(
        self,
        authorization: AuthorizationService,
        audit: AuditRecorder,
        webhook_verifier: ProviderWebhookVerifier,
    ) -> None:
        self._authorization = authorization
        self._audit = audit
        self._webhook_verifier = webhook_verifier
        self._intents: dict[tuple[str, str], PaymentIntent] = {}
        self._transactions: dict[tuple[str, str], PaymentTransaction] = {}
        self._transactions_by_key: dict[tuple[str, str], PaymentTransaction] = {}
        self._refunds: dict[tuple[str, str], PaymentRefund] = {}
        self._refunds_by_key: dict[tuple[str, str], PaymentRefund] = {}
        self._settlement_exceptions: dict[tuple[str, str], SettlementException] = {}
        self.outbox: list[PaymentEvent] = []

    def create_intent(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        intent: PaymentIntent,
        trace_id: str,
    ) -> PaymentIntent:
        self._authorize(principal_id, session_id, scope, "payment.write")
        if (
            not all((intent.intent_id, intent.order_reference, intent.currency))
            or intent.amount <= 0
            or intent.created_at.tzinfo is None
        ):
            raise PaymentValidationError(
                "payment intents require identity, order reference, currency, "
                "a positive amount, and timezone-aware time"
            )
        key = (scope.tenant_id, intent.intent_id)
        if key in self._intents:
            raise PaymentValidationError("payment intent identifiers are immutable")
        self._intents[key] = intent
        self._record(principal_id, "payment.intent.create", intent.intent_id, trace_id)
        self.outbox.append(PaymentEvent("PaymentChanged", scope.tenant_id, intent.intent_id))
        return intent

    def capture(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        callback: ProviderCallback,
        transaction: PaymentTransaction,
        trace_id: str,
    ) -> PaymentTransaction:
        self._authorize(principal_id, session_id, scope, "payment.write")
        intent = self._intent(scope, transaction.intent_id)
        if (
            not self._webhook_verifier.verify(callback)
            or callback.kind != "capture"
            or callback.subject_id != transaction.intent_id
            or callback.tenant_id != scope.tenant_id
            or callback.amount_cents != round(transaction.amount * 100)
        ):
            raise PaymentValidationError(
                "capture requires a verified provider callback for this intent"
            )
        if (
            not all(
                (
                    transaction.transaction_id,
                    transaction.idempotency_key,
                    transaction.provider_reference,
                )
            )
            or transaction.amount <= 0
            or transaction.amount > intent.amount
            or transaction.captured_at.tzinfo is None
        ):
            raise PaymentValidationError(
                "captures require identity, provider reference, a positive amount "
                "not exceeding the intent amount, and timezone-aware time"
            )
        key = (scope.tenant_id, transaction.idempotency_key)
        existing = self._transactions_by_key.get(key)
        if existing is not None:
            return existing
        transaction_key = (scope.tenant_id, transaction.transaction_id)
        if transaction_key in self._transactions:
            raise PaymentValidationError("transaction identifiers are immutable")
        self._transactions[transaction_key] = transaction
        self._transactions_by_key[key] = transaction
        self._record(principal_id, "payment.capture", transaction.transaction_id, trace_id)
        self.outbox.append(
            PaymentEvent("PaymentChanged", scope.tenant_id, transaction.transaction_id)
        )
        return transaction

    def refund(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        callback: ProviderCallback,
        refund: PaymentRefund,
        trace_id: str,
    ) -> PaymentRefund:
        self._authorize(principal_id, session_id, scope, "payment.write")
        transaction = self._transaction(scope, refund.transaction_id)
        if (
            not self._webhook_verifier.verify(callback)
            or callback.kind != "refund"
            or callback.subject_id != refund.transaction_id
            or callback.tenant_id != scope.tenant_id
            or callback.amount_cents != round(refund.amount * 100)
        ):
            raise PaymentValidationError(
                "refunds require a verified provider callback for this transaction"
            )
        already_refunded = sum(
            (
                existing_refund.amount
                for (tenant_id, _), existing_refund in self._refunds.items()
                if tenant_id == scope.tenant_id
                and existing_refund.transaction_id == refund.transaction_id
            ),
            Decimal(),
        )
        if (
            not all((refund.refund_id, refund.idempotency_key, refund.reason))
            or refund.amount <= 0
            or refund.amount + already_refunded > transaction.amount
            or refund.refunded_at.tzinfo is None
        ):
            raise PaymentValidationError(
                "refunds require identity, reason, a positive amount not exceeding "
                "the remaining captured amount, and timezone-aware time"
            )
        key = (scope.tenant_id, refund.idempotency_key)
        existing = self._refunds_by_key.get(key)
        if existing is not None:
            return existing
        refund_key = (scope.tenant_id, refund.refund_id)
        if refund_key in self._refunds:
            raise PaymentValidationError("refund identifiers are immutable")
        self._refunds[refund_key] = refund
        self._refunds_by_key[key] = refund
        self._record(principal_id, "payment.refund", refund.refund_id, trace_id)
        self.outbox.append(PaymentEvent("PaymentChanged", scope.tenant_id, refund.refund_id))
        return refund

    def report_settlement_exception(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        exception: SettlementException,
        trace_id: str,
    ) -> SettlementException:
        self._authorize(principal_id, session_id, scope, "payment.write")
        self._transaction(scope, exception.transaction_id)
        if (
            not all((exception.exception_id, exception.reason))
            or exception.expected_amount == exception.settled_amount
            or exception.reported_at.tzinfo is None
        ):
            raise PaymentValidationError(
                "settlement exceptions require identity, a reason, a real amount "
                "mismatch, and timezone-aware time"
            )
        key = (scope.tenant_id, exception.exception_id)
        existing = self._settlement_exceptions.get(key)
        if existing is not None:
            if existing == exception:
                return existing
            raise PaymentValidationError("settlement exception identifiers are immutable")
        self._settlement_exceptions[key] = exception
        self._record(
            principal_id, "payment.settlement.exception", exception.exception_id, trace_id
        )
        self.outbox.append(
            PaymentEvent(
                "SettlementReconciliationReported", scope.tenant_id, exception.exception_id
            )
        )
        return exception

    def transactions_for_intent(
        self, scope: ScopeContext, intent_id: str
    ) -> tuple[PaymentTransaction, ...]:
        self._intent(scope, intent_id)
        return tuple(
            transaction
            for (tenant_id, _), transaction in self._transactions.items()
            if tenant_id == scope.tenant_id and transaction.intent_id == intent_id
        )

    def refunds_for_transaction(
        self, scope: ScopeContext, transaction_id: str
    ) -> tuple[PaymentRefund, ...]:
        self._transaction(scope, transaction_id)
        return tuple(
            refund
            for (tenant_id, _), refund in self._refunds.items()
            if tenant_id == scope.tenant_id and refund.transaction_id == transaction_id
        )

    def _intent(self, scope: ScopeContext, intent_id: str) -> PaymentIntent:
        intent = self._intents.get((scope.tenant_id, intent_id))
        if intent is None:
            raise PaymentValidationError("payment intent is outside tenant scope or unknown")
        return intent

    def _transaction(self, scope: ScopeContext, transaction_id: str) -> PaymentTransaction:
        transaction = self._transactions.get((scope.tenant_id, transaction_id))
        if transaction is None:
            raise PaymentValidationError("transaction is outside tenant scope or unknown")
        return transaction

    def _authorize(
        self, principal_id: str, session_id: str, scope: ScopeContext, action: str
    ) -> None:
        self._authorization.authorize(principal_id, session_id, scope, action)

    def _record(self, actor_id: str, source: str, subject: str, trace_id: str) -> None:
        self._audit.record(
            actor_id,
            source,
            "payment-service",
            subject,
            "v1",
            trace_id,
            "allowed",
        )
