"""PostgreSQL persistence for immutable customer consent and stored-value facts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg

from foundation.customer import ConsentFact, Customer, PrivacyRequest, StoredValueEffect
from foundation.durable_outbox import DurableEvent, DurableOutboxStore


class DurableCustomerConsentStore:
    """Commits customer facts and their integration events in one transaction."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._outbox = DurableOutboxStore(connection)

    def create_customer(self, customer: Customer, trace_id: str) -> None:
        self._commit(customer.tenant_id, "CustomerChanged", customer.customer_id, trace_id, lambda cursor: cursor.execute("INSERT INTO customers (tenant_id, customer_id) VALUES (%s, %s)", (customer.tenant_id, customer.customer_id)))

    def record_consent(self, tenant_id: str, consent: ConsentFact, trace_id: str) -> None:
        self._commit(tenant_id, "ConsentChanged", consent.consent_id, trace_id, lambda cursor: cursor.execute("INSERT INTO consent_facts (tenant_id, consent_id, customer_id, purpose, decision, version, evidence, scope, occurred_at, replaces_consent_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (tenant_id, consent.consent_id, consent.customer_id, consent.purpose, consent.decision, consent.version, consent.evidence, consent.scope, consent.occurred_at, consent.replaces_consent_id)))

    def complete_privacy_request(self, tenant_id: str, request: PrivacyRequest, trace_id: str) -> None:
        if request.outcome is None:
            raise ValueError("privacy request outcome is required")
        self._commit(tenant_id, "CustomerChanged", request.request_id, trace_id, lambda cursor: cursor.execute("INSERT INTO privacy_requests (tenant_id, request_id, customer_id, request_type, scope, legal_evidence, outcome) VALUES (%s, %s, %s, %s, %s, %s, %s)", (tenant_id, request.request_id, request.customer_id, request.request_type, request.scope, request.legal_evidence, request.outcome)))

    def post_value(self, tenant_id: str, effect: StoredValueEffect, trace_id: str) -> None:
        self._commit(tenant_id, "StoredValueChanged", effect.effect_id, trace_id, lambda cursor: cursor.execute("INSERT INTO stored_value_effects (tenant_id, effect_id, customer_id, value_type, amount, reason, occurred_at, reversal_of_effect_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (tenant_id, effect.effect_id, effect.customer_id, effect.value_type, effect.amount, effect.reason, effect.occurred_at, effect.reversal_of_effect_id)))

    def balance(self, tenant_id: str, customer_id: str, value_type: str) -> object:
        connection = self._outbox._connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM stored_value_effects WHERE tenant_id = %s AND customer_id = %s AND value_type = %s", (tenant_id, customer_id, value_type))
            return cursor.fetchone()[0]

    def _commit(self, tenant_id: str, event_type: str, subject_id: str, trace_id: str, write: Any) -> None:
        event = DurableEvent(f"{event_type}-{tenant_id}-{subject_id}", tenant_id, event_type, "v1", trace_id, {"subject_id": subject_id}, datetime.now(UTC))
        self._outbox.commit_business_event(event, write)
