"""Tenant-scoped support conversations, cases, and governed campaign publication."""

from __future__ import annotations

from dataclasses import dataclass

from foundation.audit import AuditRecorder
from foundation.communications import CommunicationService
from foundation.organization import ScopeContext, ScopeDeniedError


class EngagementError(ValueError):
    """Raised when a governed engagement operation is invalid."""


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    conversation_id: str
    tenant_id: str
    direction: str
    sequence: int
    customer_id: str
    order_id: str | None
    body: str


@dataclass(frozen=True)
class SupportCase:
    case_id: str
    tenant_id: str
    conversation_id: str
    customer_id: str
    order_id: str | None
    assignee_id: str | None
    priority: str
    status: str
    target_sequence: int
    escalation: str | None = None


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    tenant_id: str
    template_id: str
    status: str
    audience: tuple[str, ...]
    published_by: str | None = None


@dataclass(frozen=True)
class EngagementEvent:
    tenant_id: str
    entity_id: str
    event_type: str
    trace_id: str


class EngagementService:
    """Owns ordered conversation facts, case workflow, and consent-first campaigns."""

    def __init__(self, audit: AuditRecorder, communications: CommunicationService) -> None:
        self._audit = audit
        self._communications = communications
        self._messages: dict[str, ConversationMessage] = {}
        self._conversation_sequences: dict[tuple[str, str], int] = {}
        self._cases: dict[str, SupportCase] = {}
        self._campaigns: dict[str, Campaign] = {}
        self._campaign_keys: dict[tuple[str, str], Campaign] = {}
        self._contacts: dict[tuple[str, str], tuple[str, bool, bool]] = {}
        self.outbox: list[EngagementEvent] = []

    def register_contact(
        self,
        scope: ScopeContext,
        contact_id: str,
        recipient: str,
        consented: bool,
        suppressed: bool,
    ) -> None:
        self._require_admin(scope)
        self._contacts[(scope.tenant_id, contact_id)] = (recipient, consented, suppressed)

    def record_message(
        self,
        scope: ScopeContext,
        message_id: str,
        conversation_id: str,
        direction: str,
        sequence: int,
        customer_id: str,
        order_id: str | None,
        body: str,
        trace_id: str,
    ) -> ConversationMessage:
        key = (scope.tenant_id, conversation_id)
        if not message_id or message_id in self._messages or direction not in {"inbound", "outbound"}:
            raise EngagementError("Message identity and direction are required.")
        if sequence <= self._conversation_sequences.get(key, 0):
            raise EngagementError("Conversation messages must be strictly ordered.")
        message = ConversationMessage(
            message_id, conversation_id, scope.tenant_id, direction, sequence, customer_id, order_id, body
        )
        self._messages[message_id] = message
        self._conversation_sequences[key] = sequence
        self._emit(scope, message_id, "conversation.message.recorded", trace_id)
        return message

    def create_case(
        self,
        scope: ScopeContext,
        case_id: str,
        conversation_id: str,
        customer_id: str,
        order_id: str | None,
        priority: str,
        target_sequence: int,
        trace_id: str,
    ) -> SupportCase:
        if case_id in self._cases or priority not in {"low", "medium", "high", "urgent"}:
            raise EngagementError("Case identity and supported priority are required.")
        case = SupportCase(
            case_id, scope.tenant_id, conversation_id, customer_id, order_id, None, priority, "open", target_sequence
        )
        self._cases[case_id] = case
        self._emit(scope, case_id, "case.changed", trace_id)
        return case

    def assign_case(
        self,
        scope: ScopeContext,
        case_id: str,
        assignee_id: str,
        actor_id: str,
        trace_id: str,
    ) -> SupportCase:
        case = self._case(scope, case_id)
        if not assignee_id or not actor_id:
            raise ScopeDeniedError("An authorized actor and assignee are required.")
        return self._replace_case(case, assignee_id=assignee_id, trace_id=trace_id)

    def transition_case(
        self,
        scope: ScopeContext,
        case_id: str,
        status: str,
        current_sequence: int,
        trace_id: str,
    ) -> SupportCase:
        case = self._case(scope, case_id)
        allowed = {"open": {"pending", "resolved"}, "pending": {"open", "resolved"}}
        if status not in allowed.get(case.status, set()):
            raise EngagementError("Case transition is not allowed.")
        escalation = "breached" if current_sequence > case.target_sequence else None
        return self._replace_case(case, status=status, escalation=escalation, trace_id=trace_id)

    def service_status(self, scope: ScopeContext, case_id: str, current_sequence: int) -> str:
        case = self._case(scope, case_id)
        if current_sequence > case.target_sequence:
            return "breached"
        if current_sequence == case.target_sequence:
            return "approaching"
        return "on_target"

    def create_campaign(
        self,
        scope: ScopeContext,
        campaign_id: str,
        template_id: str,
        contact_ids: list[str],
        idempotency_key: str,
        trace_id: str,
    ) -> Campaign:
        self._require_admin(scope)
        key = (scope.tenant_id, idempotency_key)
        if key in self._campaign_keys:
            return self._campaign_keys[key]
        audience = tuple(
            contact_id
            for contact_id in contact_ids
            if self._eligible(scope.tenant_id, contact_id)
        )
        campaign = Campaign(campaign_id, scope.tenant_id, template_id, "draft", audience)
        self._campaigns[campaign_id] = campaign
        self._campaign_keys[key] = campaign
        self._emit(scope, campaign_id, "campaign.created", trace_id)
        return campaign

    def publish_campaign(
        self,
        scope: ScopeContext,
        campaign_id: str,
        authority_id: str,
        trace_id: str,
    ) -> Campaign:
        self._require_admin(scope)
        campaign = self._campaign(scope, campaign_id)
        if campaign.status != "draft" or not authority_id:
            raise EngagementError("Draft campaigns require publishing authority.")
        updated = Campaign(
            campaign.campaign_id,
            campaign.tenant_id,
            campaign.template_id,
            "published",
            campaign.audience,
            authority_id,
        )
        self._campaigns[campaign_id] = updated
        self._audit.record(authority_id, "engagement", "campaign", "publish", "consent", trace_id, campaign_id)
        self._emit(scope, campaign_id, "campaign.published", trace_id)
        return updated

    def recover_open_cases(self, scope: ScopeContext) -> list[SupportCase]:
        return [item for item in self._cases.values() if item.tenant_id == scope.tenant_id and item.status != "resolved"]

    def _eligible(self, tenant_id: str, contact_id: str) -> bool:
        contact = self._contacts.get((tenant_id, contact_id))
        return contact is not None and contact[1] and not contact[2]

    def _case(self, scope: ScopeContext, case_id: str) -> SupportCase:
        case = self._cases.get(case_id)
        if case is None or case.tenant_id != scope.tenant_id:
            raise ScopeDeniedError("Case is outside the tenant scope.")
        return case

    def _campaign(self, scope: ScopeContext, campaign_id: str) -> Campaign:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None or campaign.tenant_id != scope.tenant_id:
            raise ScopeDeniedError("Campaign is outside the tenant scope.")
        return campaign

    def _replace_case(
        self,
        case: SupportCase,
        assignee_id: str | None = None,
        status: str | None = None,
        escalation: str | None = None,
        trace_id: str = "",
    ) -> SupportCase:
        updated = SupportCase(
            case.case_id,
            case.tenant_id,
            case.conversation_id,
            case.customer_id,
            case.order_id,
            assignee_id if assignee_id is not None else case.assignee_id,
            case.priority,
            status if status is not None else case.status,
            case.target_sequence,
            escalation,
        )
        self._cases[case.case_id] = updated
        self._emit(ScopeContext(case.tenant_id), case.case_id, "case.changed", trace_id)
        return updated

    def _emit(self, scope: ScopeContext, entity_id: str, event_type: str, trace_id: str) -> None:
        self.outbox.append(EngagementEvent(scope.tenant_id, entity_id, event_type, trace_id))

    @staticmethod
    def _require_admin(scope: ScopeContext) -> None:
        if not scope.is_tenant_administrator:
            raise ScopeDeniedError("Tenant administration is required.")
