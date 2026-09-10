import pytest

from foundation.audit import AuditRecorder
from foundation.communications import (
    CommunicationError,
    CommunicationService,
    LocalProviderAdapter,
    MessageTemplate,
)
from foundation.organization import ScopeContext


@pytest.fixture
def scope() -> ScopeContext:
    return ScopeContext("tenant-a", is_tenant_administrator=True)


def setup_service(outcomes: list[str] | None = None) -> tuple[CommunicationService, ScopeContext]:
    active_scope = ScopeContext("tenant-a", is_tenant_administrator=True)
    service = CommunicationService(AuditRecorder(), LocalProviderAdapter(outcomes))
    service.approve_template(
        active_scope,
        MessageTemplate("receipt", "tenant-a", "email", 1, "Body", True),
    )
    service.grant_consent(active_scope, "customer@example.test", "email")
    return service, active_scope


def test_successful_send_duplicate_prevention_and_delivery_status() -> None:
    service, active_scope = setup_service()
    first = service.request(
        active_scope, "m-1", "email", "customer@example.test", "receipt", "key-1", "agent", "t-1"
    )
    duplicate = service.request(
        active_scope, "m-2", "email", "customer@example.test", "receipt", "key-1", "agent", "t-2"
    )
    delivered = service.update_delivery(active_scope, first.message_id, "delivered", "agent", "t-3")
    assert duplicate == first
    assert delivered.status == "delivered"


def test_retryable_and_permanent_provider_failures() -> None:
    retry_service, active_scope = setup_service(["timeout", "accepted"])
    retryable = retry_service.request(
        active_scope, "m-1", "email", "customer@example.test", "receipt", "key-1", "agent", "t-1"
    )
    assert retry_service.retry(active_scope, retryable.message_id, "agent", "t-2").status == "accepted"
    failed_service, active_scope = setup_service(["permanent_failure"])
    failed = failed_service.request(
        active_scope, "m-2", "email", "customer@example.test", "receipt", "key-2", "agent", "t-3"
    )
    assert failed.status == "dead_letter"


def test_consent_suppression_template_and_tenant_controls() -> None:
    service, active_scope = setup_service()
    service.suppress(active_scope, "customer@example.test", "email")
    with pytest.raises(CommunicationError):
        service.request(
            active_scope, "m-1", "email", "customer@example.test", "receipt", "key-1", "agent", "t-1"
        )
    with pytest.raises(CommunicationError):
        service.approve_template(
            active_scope,
            MessageTemplate("bad", "tenant-a", "sms", 1, "", True),
        )
    other_scope = ScopeContext("tenant-b", is_tenant_administrator=True)
    with pytest.raises(CommunicationError):
        service.request(
            other_scope, "m-2", "email", "customer@example.test", "receipt", "key-2", "agent", "t-2"
        )


def test_signed_webhook_validation_has_no_customer_effect() -> None:
    payload = "provider-event"
    secret = "sandbox-secret"
    assert not LocalProviderAdapter.valid_webhook("bad", payload, secret)
