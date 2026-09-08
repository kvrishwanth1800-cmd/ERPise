"""Tenant-scoped customer identity, consent, privacy, and stored-value services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class CustomerValidationError(ValueError):
    """Raised when a customer command cannot preserve the required evidence."""


@dataclass(frozen=True)
class Customer:
    customer_id: str
    tenant_id: str


@dataclass(frozen=True)
class IdentityHistory:
    history_id: str
    customer_id: str
    related_customer_id: str
    action: str
    occurred_at: datetime


@dataclass(frozen=True)
class ConsentFact:
    consent_id: str
    customer_id: str
    purpose: str
    decision: str
    version: str
    evidence: str
    scope: str
    occurred_at: datetime
    replaces_consent_id: str | None = None


@dataclass(frozen=True)
class PrivacyRequest:
    request_id: str
    customer_id: str
    request_type: str
    scope: str
    legal_evidence: str
    outcome: str | None = None


@dataclass(frozen=True)
class StoredValueEffect:
    effect_id: str
    customer_id: str
    value_type: str
    amount: Decimal
    reason: str
    occurred_at: datetime
    reversal_of_effect_id: str | None = None


@dataclass(frozen=True)
class CustomerEvent:
    event_type: str
    tenant_id: str
    subject_id: str


class CustomerConsentService:
    """Records immutable customer facts and derives tenant-scoped current state."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._customers: dict[tuple[str, str], Customer] = {}
        self._identity_history: dict[tuple[str, str], IdentityHistory] = {}
        self._consents: dict[tuple[str, str], ConsentFact] = {}
        self._privacy_requests: dict[tuple[str, str], PrivacyRequest] = {}
        self._value_effects: dict[tuple[str, str], StoredValueEffect] = {}
        self.outbox: list[CustomerEvent] = []

    def create_customer(self, principal_id: str, session_id: str, scope: ScopeContext, customer_id: str, trace_id: str) -> Customer:
        self._authorize(principal_id, session_id, scope, "customer.write")
        key = (scope.tenant_id, customer_id)
        if not customer_id or key in self._customers:
            raise CustomerValidationError("customer identifiers must be unique and non-empty")
        customer = Customer(customer_id, scope.tenant_id)
        self._customers[key] = customer
        self._record(principal_id, "customer.create", customer_id, trace_id)
        self.outbox.append(CustomerEvent("CustomerChanged", scope.tenant_id, customer_id))
        return customer

    def merge(self, principal_id: str, session_id: str, scope: ScopeContext, history_id: str, surviving_customer_id: str, merged_customer_id: str, occurred_at: datetime, trace_id: str) -> IdentityHistory:
        return self._identity(principal_id, session_id, scope, history_id, surviving_customer_id, merged_customer_id, "merged", occurred_at, trace_id)

    def unmerge(self, principal_id: str, session_id: str, scope: ScopeContext, history_id: str, customer_id: str, restored_customer_id: str, occurred_at: datetime, trace_id: str) -> IdentityHistory:
        return self._identity(principal_id, session_id, scope, history_id, customer_id, restored_customer_id, "unmerged", occurred_at, trace_id)

    def record_consent(self, principal_id: str, session_id: str, scope: ScopeContext, consent: ConsentFact, trace_id: str) -> ConsentFact:
        self._authorize(principal_id, session_id, scope, "consent.write")
        self._customer(scope, consent.customer_id)
        if not all((consent.consent_id, consent.purpose, consent.decision, consent.version, consent.evidence, consent.scope)) or consent.decision not in {"granted", "withdrawn"} or consent.occurred_at.tzinfo is None:
            raise CustomerValidationError("consent requires purpose, decision, version, evidence, scope, and timezone-aware time")
        key = (scope.tenant_id, consent.consent_id)
        existing = self._consents.get(key)
        if existing is not None:
            if existing == consent:
                return existing
            raise CustomerValidationError("consent identifiers are immutable")
        if consent.replaces_consent_id is not None and (scope.tenant_id, consent.replaces_consent_id) not in self._consents:
            raise CustomerValidationError("replacement consent must reference an existing tenant consent")
        self._consents[key] = consent
        self._record(principal_id, "consent.record", consent.consent_id, trace_id)
        self.outbox.append(CustomerEvent("ConsentChanged", scope.tenant_id, consent.consent_id))
        return consent

    def has_consent(self, scope: ScopeContext, customer_id: str, purpose: str) -> bool:
        self._customer(scope, customer_id)
        facts = [fact for (tenant_id, _), fact in self._consents.items() if tenant_id == scope.tenant_id and fact.customer_id == customer_id and fact.purpose == purpose]
        if not facts:
            return False
        latest = max(facts, key=lambda fact: (fact.occurred_at, fact.consent_id))
        return latest.decision == "granted"

    def complete_privacy_request(self, principal_id: str, session_id: str, scope: ScopeContext, request: PrivacyRequest, outcome: str, trace_id: str) -> PrivacyRequest:
        self._authorize(principal_id, session_id, scope, "privacy.write")
        self._customer(scope, request.customer_id)
        if request.request_type not in {"export", "deletion"} or not request.scope or not request.legal_evidence or outcome not in {"exported", "deleted", "rejected"}:
            raise CustomerValidationError("privacy requests require a valid type, scope, legal evidence, and outcome")
        key = (scope.tenant_id, request.request_id)
        existing = self._privacy_requests.get(key)
        completed = PrivacyRequest(request.request_id, request.customer_id, request.request_type, request.scope, request.legal_evidence, outcome)
        if existing is not None:
            if existing == completed:
                return existing
            raise CustomerValidationError("privacy request identifiers are immutable")
        self._privacy_requests[key] = completed
        self._record(principal_id, "privacy.complete", request.request_id, trace_id)
        self.outbox.append(CustomerEvent("CustomerChanged", scope.tenant_id, request.request_id))
        return completed

    def post_value(self, principal_id: str, session_id: str, scope: ScopeContext, effect: StoredValueEffect, trace_id: str) -> StoredValueEffect:
        self._authorize(principal_id, session_id, scope, "stored_value.write")
        self._customer(scope, effect.customer_id)
        if not all((effect.effect_id, effect.value_type, effect.reason)) or effect.amount == 0 or effect.occurred_at.tzinfo is None:
            raise CustomerValidationError("value effects require identity, type, reason, non-zero amount, and timezone-aware time")
        key = (scope.tenant_id, effect.effect_id)
        existing = self._value_effects.get(key)
        if existing is not None:
            if existing == effect:
                return existing
            raise CustomerValidationError("stored value effect identifiers are immutable")
        if effect.reversal_of_effect_id is not None:
            original = self._value_effects.get((scope.tenant_id, effect.reversal_of_effect_id))
            if original is None or original.customer_id != effect.customer_id or original.value_type != effect.value_type or effect.amount != -original.amount:
                raise CustomerValidationError("reversal must link to and exactly offset its original effect")
        self._value_effects[key] = effect
        self._record(principal_id, "stored_value.post", effect.effect_id, trace_id)
        self.outbox.append(CustomerEvent("StoredValueChanged", scope.tenant_id, effect.effect_id))
        return effect

    def reverse_value(self, principal_id: str, session_id: str, scope: ScopeContext, original_effect_id: str, reversal_effect_id: str, reason: str, occurred_at: datetime, trace_id: str) -> StoredValueEffect:
        original = self._value_effects.get((scope.tenant_id, original_effect_id))
        if original is None:
            raise CustomerValidationError("stored value effect to reverse is unknown")
        return self.post_value(principal_id, session_id, scope, StoredValueEffect(reversal_effect_id, original.customer_id, original.value_type, -original.amount, reason, occurred_at, original_effect_id), trace_id)

    def balance(self, scope: ScopeContext, customer_id: str, value_type: str) -> Decimal:
        self._customer(scope, customer_id)
        return sum((effect.amount for (tenant_id, _), effect in self._value_effects.items() if tenant_id == scope.tenant_id and effect.customer_id == customer_id and effect.value_type == value_type), Decimal())

    def _identity(self, principal_id: str, session_id: str, scope: ScopeContext, history_id: str, customer_id: str, related_customer_id: str, action: str, occurred_at: datetime, trace_id: str) -> IdentityHistory:
        self._authorize(principal_id, session_id, scope, "customer.write")
        self._customer(scope, customer_id)
        self._customer(scope, related_customer_id)
        if not history_id or customer_id == related_customer_id or occurred_at.tzinfo is None:
            raise CustomerValidationError("identity history requires distinct customers and timezone-aware time")
        key = (scope.tenant_id, history_id)
        history = IdentityHistory(history_id, customer_id, related_customer_id, action, occurred_at)
        if key in self._identity_history:
            raise CustomerValidationError("identity history identifiers are immutable")
        self._identity_history[key] = history
        self._record(principal_id, f"customer.{action}", history_id, trace_id)
        self.outbox.append(CustomerEvent("CustomerChanged", scope.tenant_id, history_id))
        return history

    def _customer(self, scope: ScopeContext, customer_id: str) -> Customer:
        customer = self._customers.get((scope.tenant_id, customer_id))
        if customer is None:
            raise CustomerValidationError("customer is outside tenant scope or unknown")
        return customer

    def _authorize(self, principal_id: str, session_id: str, scope: ScopeContext, action: str) -> None:
        self._authorization.authorize(principal_id, session_id, scope, action)

    def _record(self, actor_id: str, source: str, subject: str, trace_id: str) -> None:
        self._audit.record(actor_id, source, "customer-service", subject, "v1", trace_id, "allowed")
