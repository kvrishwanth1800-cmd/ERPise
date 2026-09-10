from pathlib import Path

import pytest

from foundation.audit import AuditRecorder
from foundation.communications import CommunicationService, LocalProviderAdapter
from foundation.engagement import EngagementError, EngagementService
from foundation.organization import ScopeContext, ScopeDeniedError


@pytest.fixture
def scope() -> ScopeContext:
    return ScopeContext("tenant-a", is_tenant_administrator=True)


@pytest.fixture
def service() -> EngagementService:
    audit = AuditRecorder()
    return EngagementService(audit, CommunicationService(audit, LocalProviderAdapter()))


def test_conversation_order_case_lifecycle_and_sla(
    service: EngagementService,
    scope: ScopeContext,
) -> None:
    message = service.record_message(
        scope,
        "message-1",
        "conversation-1",
        "inbound",
        1,
        "customer-1",
        "order-1",
        "help",
        "trace-1",
    )
    assert message.direction == "inbound"
    with pytest.raises(EngagementError):
        service.record_message(
            scope,
            "message-2",
            "conversation-1",
            "outbound",
            1,
            "customer-1",
            None,
            "reply",
            "trace-2",
        )
    case = service.create_case(
        scope,
        "case-1",
        "conversation-1",
        "customer-1",
        "order-1",
        "high",
        2,
        "trace-3",
    )
    assigned = service.assign_case(scope, case.case_id, "agent-1", "manager-1", "trace-4")
    assert assigned.assignee_id == "agent-1"
    assert service.service_status(scope, case.case_id, 2) == "approaching"
    pending = service.transition_case(scope, case.case_id, "pending", 3, "trace-5")
    assert pending.escalation == "breached"
    assert service.transition_case(scope, case.case_id, "resolved", 3, "trace-6").status == "resolved"
    with pytest.raises(EngagementError):
        service.transition_case(scope, case.case_id, "open", 3, "trace-7")


def test_campaign_filters_contacts_prevents_duplicates_and_requires_authority(
    service: EngagementService,
    scope: ScopeContext,
) -> None:
    service.register_contact(scope, "allowed", "allowed@example.test", True, False)
    service.register_contact(scope, "suppressed", "suppressed@example.test", True, True)
    service.register_contact(scope, "no-consent", "none@example.test", False, False)
    campaign = service.create_campaign(
        scope,
        "campaign-1",
        "receipt",
        ["allowed", "suppressed", "no-consent"],
        "key-1",
        "trace-1",
    )
    assert campaign.audience == ("allowed",)
    duplicate = service.create_campaign(scope, "campaign-2", "receipt", [], "key-1", "trace-2")
    assert duplicate == campaign
    with pytest.raises(EngagementError):
        service.publish_campaign(scope, campaign.campaign_id, "", "trace-3")
    published = service.publish_campaign(scope, campaign.campaign_id, "marketing-admin", "trace-4")
    assert published.status == "published"


def test_tenant_isolation_restart_recovery_and_migration() -> None:
    audit = AuditRecorder()
    service = EngagementService(audit, CommunicationService(audit, LocalProviderAdapter()))
    admin = ScopeContext("tenant-a", is_tenant_administrator=True)
    service.create_case(
        admin,
        "case-1",
        "conversation-1",
        "customer-1",
        None,
        "medium",
        2,
        "trace",
    )
    assert len(service.recover_open_cases(admin)) == 1
    outsider = ScopeContext("tenant-b", is_tenant_administrator=True)
    with pytest.raises(ScopeDeniedError):
        service.service_status(outsider, "case-1", 1)
    migration = (Path(__file__).parents[1] / "migrations" / "0003_engagement.sql").read_text()
    assert "engagement_campaigns" in migration and "-- DOWN" in migration
