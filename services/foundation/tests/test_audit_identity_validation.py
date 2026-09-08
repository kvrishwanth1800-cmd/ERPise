import pytest
from foundation.audit import ApprovalStateError, ApprovalWorkflowService, AuditRecorder


def test_approval_rejects_blank_approver_without_resolving_the_request() -> None:
    service = ApprovalWorkflowService(AuditRecorder())
    service.create("approval-a", "requester-a", "payment", "retry", "trace-a")

    with pytest.raises(ApprovalStateError, match="approver and trace identifiers"):
        service.approve("approval-a", "", "trace-b")

    resolved = service.approve("approval-a", "approver-a", "trace-c")
    assert resolved.status == "approved"
    assert resolved.resolver_id == "approver-a"


def test_approval_creation_rejects_blank_requester_or_trace_context() -> None:
    service = ApprovalWorkflowService(AuditRecorder())

    with pytest.raises(ApprovalStateError, match="requester, policy, and trace identifiers"):
        service.create("approval-a", "", "payment", "retry", "trace-a")

    with pytest.raises(ApprovalStateError, match="requester, policy, and trace identifiers"):
        service.create("approval-b", "requester-a", "payment", "retry", "")
