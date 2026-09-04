"""Append-only audit evidence and policy-controlled approval workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


class SelfApprovalError(PermissionError):
    """Raised when a requester attempts to resolve their own approval."""


class ApprovalStateError(ValueError):
    """Raised when a workflow transition is invalid."""


@dataclass(frozen=True)
class AuditRecord:
    actor_id: str
    authority: str
    source: str
    reason: str
    policy: str
    occurred_at: datetime
    trace_id: str
    result: str


@dataclass(frozen=True)
class AuditRecorded:
    record: AuditRecord


class AuditRecorder:
    """Appends immutable operational evidence without update or delete operations."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self.outbox: list[AuditRecorded] = []

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        actor_id: str,
        authority: str,
        source: str,
        reason: str,
        policy: str,
        trace_id: str,
        result: str,
    ) -> AuditRecord:
        record = AuditRecord(
            actor_id=actor_id,
            authority=authority,
            source=source,
            reason=reason,
            policy=policy,
            occurred_at=datetime.now(UTC),
            trace_id=trace_id,
            result=result,
        )
        self._records.append(record)
        self.outbox.append(AuditRecorded(record=record))
        return record


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    requester_id: str
    policy: str
    timeout_outcome: str
    status: str = "pending"
    resolver_id: str | None = None


@dataclass(frozen=True)
class ApprovalResolved:
    approval_id: str
    outcome: str


class ApprovalWorkflowService:
    """Creates and resolves approval requests while recording every transition."""

    _timeout_outcomes = frozenset({"retry", "escalate", "compensate"})

    def __init__(self, audit_recorder: AuditRecorder) -> None:
        self._audit_recorder = audit_recorder
        self._requests: dict[str, ApprovalRequest] = {}
        self.outbox: list[ApprovalResolved] = []

    def create(
        self,
        approval_id: str,
        requester_id: str,
        policy: str,
        timeout_outcome: str,
        trace_id: str,
    ) -> ApprovalRequest:
        if not approval_id or approval_id in self._requests:
            raise ApprovalStateError("Approval identifiers must be unique and non-empty.")
        if timeout_outcome not in self._timeout_outcomes:
            raise ApprovalStateError("Timeout outcome must be retry, escalate, or compensate.")
        request = ApprovalRequest(approval_id, requester_id, policy, timeout_outcome)
        self._requests[approval_id] = request
        self._record_transition(request, requester_id, trace_id, "created")
        return request

    def approve(
        self,
        approval_id: str,
        approver_id: str,
        trace_id: str,
    ) -> ApprovalRequest:
        request = self._pending_request(approval_id)
        if approver_id == request.requester_id:
            raise SelfApprovalError("Requesters cannot approve their own requests.")
        resolved = ApprovalRequest(
            request.approval_id,
            request.requester_id,
            request.policy,
            request.timeout_outcome,
            status="approved",
            resolver_id=approver_id,
        )
        self._requests[approval_id] = resolved
        self._record_transition(resolved, approver_id, trace_id, "approved")
        self.outbox.append(ApprovalResolved(approval_id=approval_id, outcome="approved"))
        return resolved

    def timeout(self, approval_id: str, trace_id: str) -> ApprovalRequest:
        request = self._pending_request(approval_id)
        resolved = ApprovalRequest(
            request.approval_id,
            request.requester_id,
            request.policy,
            request.timeout_outcome,
            status=request.timeout_outcome,
        )
        self._requests[approval_id] = resolved
        self._record_transition(resolved, "workflow", trace_id, request.timeout_outcome)
        self.outbox.append(ApprovalResolved(approval_id=approval_id, outcome=request.timeout_outcome))
        return resolved

    def _pending_request(self, approval_id: str) -> ApprovalRequest:
        request = self._requests.get(approval_id)
        if request is None or request.status != "pending":
            raise ApprovalStateError("Only pending approval requests can be resolved.")
        return request

    def _record_transition(
        self,
        request: ApprovalRequest,
        actor_id: str,
        trace_id: str,
        result: str,
    ) -> None:
        self._audit_recorder.record(
            actor_id=actor_id,
            authority="approval-workflow",
            source="approval-request",
            reason="policy-controlled action",
            policy=request.policy,
            trace_id=trace_id,
            result=result,
        )
