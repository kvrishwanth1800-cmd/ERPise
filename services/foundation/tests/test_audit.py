from dataclasses import FrozenInstanceError

import pytest
from foundation.audit import (
    ApprovalWorkflowService,
    AuditRecorder,
    SelfApprovalError,
)


def test_audit_recorder_captures_required_immutable_evidence() -> None:
    recorder = AuditRecorder()
    record = recorder.record(
        actor_id="actor-a",
        authority="tenant-admin",
        source="purchase-order",
        reason="submit",
        policy="purchase-approval",
        trace_id="trace-a",
        result="attempted",
    )

    assert record in recorder.records
    assert record.occurred_at.tzinfo is not None
    with pytest.raises(FrozenInstanceError):
        record.result = "changed"  # type: ignore[misc]


def test_requester_cannot_approve_own_request() -> None:
    service = ApprovalWorkflowService(AuditRecorder())
    service.create("approval-a", "requester-a", "payment", "escalate", "trace-a")

    with pytest.raises(SelfApprovalError):
        service.approve("approval-a", "requester-a", "trace-b")


def test_timeout_applies_defined_escalation_outcome() -> None:
    recorder = AuditRecorder()
    service = ApprovalWorkflowService(recorder)
    service.create("approval-a", "requester-a", "payment", "escalate", "trace-a")

    resolved = service.timeout("approval-a", "trace-b")

    assert resolved.status == "escalate"
    assert service.outbox[-1].outcome == "escalate"
    assert recorder.records[-1].result == "escalate"


def test_eligible_approver_resolves_request_and_records_audit_evidence() -> None:
    recorder = AuditRecorder()
    service = ApprovalWorkflowService(recorder)
    service.create("approval-a", "requester-a", "payment", "retry", "trace-a")

    resolved = service.approve("approval-a", "approver-a", "trace-b")

    assert resolved.status == "approved"
    assert resolved.resolver_id == "approver-a"
    assert recorder.records[-1].actor_id == "approver-a"
