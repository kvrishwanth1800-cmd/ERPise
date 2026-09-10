"""Governed, consent-aware communication adapters with local provider fakes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest

from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext, ScopeDeniedError


class CommunicationError(ValueError):
    """Raised for an invalid governed communication request."""


class RetryableProviderError(CommunicationError):
    """Raised when the local provider operation may be retried."""


@dataclass(frozen=True)
class MessageTemplate:
    template_id: str
    tenant_id: str
    channel: str
    version: int
    body: str
    approved: bool


@dataclass(frozen=True)
class MessageRequest:
    message_id: str
    tenant_id: str
    channel: str
    recipient: str
    template_id: str
    idempotency_key: str
    status: str
    attempts: int = 0
    provider_message_id: str | None = None
    failure_kind: str | None = None


@dataclass(frozen=True)
class CommunicationEvent:
    message_id: str
    tenant_id: str
    status: str
    trace_id: str


class LocalProviderAdapter:
    """Sandbox adapter that returns configured outcomes without exposing credentials."""

    def __init__(self, outcomes: list[str] | None = None) -> None:
        self._outcomes = outcomes or ["accepted"]
        self.sent: list[tuple[str, str, str]] = []

    def send(self, channel: str, recipient: str, body: str) -> str:
        outcome = self._outcomes.pop(0) if self._outcomes else "accepted"
        if outcome == "timeout" or outcome == "rate_limited":
            raise RetryableProviderError(outcome)
        if outcome == "permanent_failure":
            raise CommunicationError(outcome)
        provider_message_id = f"local-{len(self.sent) + 1}"
        self.sent.append((channel, recipient, body))
        return provider_message_id

    @staticmethod
    def valid_webhook(signature: str, payload: str, secret: str) -> bool:
        expected = sha256(f"{secret}:{payload}".encode()).hexdigest()
        return compare_digest(signature, expected)


class CommunicationService:
    """Enforces template, consent, suppression, channel, retry, and audit controls."""

    def __init__(self, audit: AuditRecorder, provider: LocalProviderAdapter) -> None:
        self._audit = audit
        self._provider = provider
        self._templates: dict[tuple[str, str], MessageTemplate] = {}
        self._consents: set[tuple[str, str, str]] = set()
        self._suppressed: set[tuple[str, str, str]] = set()
        self._messages: dict[str, MessageRequest] = {}
        self._idempotent: dict[tuple[str, str], MessageRequest] = {}
        self.outbox: list[CommunicationEvent] = []

    def approve_template(self, scope: ScopeContext, template: MessageTemplate) -> None:
        self._require_admin(scope)
        if template.tenant_id != scope.tenant_id or not template.body or not template.approved:
            raise CommunicationError("Templates must be approved, scoped, and non-empty.")
        self._templates[(scope.tenant_id, template.template_id)] = template

    def grant_consent(self, scope: ScopeContext, recipient: str, channel: str) -> None:
        self._require_admin(scope)
        self._consents.add((scope.tenant_id, recipient, channel))

    def suppress(self, scope: ScopeContext, recipient: str, channel: str) -> None:
        self._require_admin(scope)
        self._suppressed.add((scope.tenant_id, recipient, channel))

    def request(
        self,
        scope: ScopeContext,
        message_id: str,
        channel: str,
        recipient: str,
        template_id: str,
        idempotency_key: str,
        actor_id: str,
        trace_id: str,
    ) -> MessageRequest:
        self._require_scope(scope, actor_id)
        key = (scope.tenant_id, idempotency_key)
        if key in self._idempotent:
            return self._idempotent[key]
        template = self._templates.get((scope.tenant_id, template_id))
        allowed = (scope.tenant_id, recipient, channel) in self._consents
        suppressed = (scope.tenant_id, recipient, channel) in self._suppressed
        if not message_id or message_id in self._messages or not template or not allowed or suppressed:
            raise CommunicationError("Message requires approved template, consent, and no suppression.")
        message = MessageRequest(
            message_id,
            scope.tenant_id,
            channel,
            recipient,
            template_id,
            idempotency_key,
            "queued",
        )
        self._messages[message_id] = message
        self._idempotent[key] = message
        return self._send(message, template.body, actor_id, trace_id)

    def retry(
        self, scope: ScopeContext, message_id: str, actor_id: str, trace_id: str
    ) -> MessageRequest:
        message = self._get(scope, message_id)
        if message.status != "retryable":
            raise CommunicationError("Only retryable messages can be retried.")
        template = self._templates[(scope.tenant_id, message.template_id)]
        return self._send(message, template.body, actor_id, trace_id)

    def update_delivery(
        self, scope: ScopeContext, message_id: str, status: str, actor_id: str, trace_id: str
    ) -> MessageRequest:
        message = self._get(scope, message_id)
        if status not in {"delivered", "undeliverable"} or message.status != "accepted":
            raise CommunicationError("Delivery updates require an accepted message and supported status.")
        return self._record(message, status, actor_id, trace_id)

    def _send(self, message: MessageRequest, body: str, actor_id: str, trace_id: str) -> MessageRequest:
        try:
            provider_id = self._provider.send(message.channel, message.recipient, body)
        except RetryableProviderError as error:
            return self._record(message, "retryable", actor_id, trace_id, str(error))
        except CommunicationError as error:
            return self._record(message, "dead_letter", actor_id, trace_id, str(error))
        return self._record(message, "accepted", actor_id, trace_id, provider_message_id=provider_id)

    def _record(
        self,
        message: MessageRequest,
        status: str,
        actor_id: str,
        trace_id: str,
        failure_kind: str | None = None,
        provider_message_id: str | None = None,
    ) -> MessageRequest:
        updated = MessageRequest(
            message.message_id,
            message.tenant_id,
            message.channel,
            message.recipient,
            message.template_id,
            message.idempotency_key,
            status,
            message.attempts + 1,
            provider_message_id or message.provider_message_id,
            failure_kind,
        )
        self._messages[message.message_id] = updated
        self._audit.record(actor_id, "communications", "adapter", "governed send", "consent", trace_id, status)
        self.outbox.append(CommunicationEvent(message.message_id, message.tenant_id, status, trace_id))
        return updated

    def _get(self, scope: ScopeContext, message_id: str) -> MessageRequest:
        message = self._messages.get(message_id)
        if message is None or message.tenant_id != scope.tenant_id:
            raise ScopeDeniedError("Message is outside the authorized tenant scope.")
        return message

    @staticmethod
    def _require_admin(scope: ScopeContext) -> None:
        if not scope.is_tenant_administrator:
            raise ScopeDeniedError("Tenant administration is required for communication controls.")

    @staticmethod
    def _require_scope(scope: ScopeContext, actor_id: str) -> None:
        if not scope.tenant_id or not actor_id:
            raise ScopeDeniedError("Tenant scope and actor identity are required.")
