# ruff: noqa: E501, I001
"""Versioned subprocess boundary for the Rust reservation authority.

This adapter owns process transport and durable command replay only. Reservation
state transitions, availability, concurrency, and idempotency remain in
``transaction-core::ReservationEngine``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
from threading import Lock
from typing import Literal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class ReservationRuntimeError(RuntimeError):
    pass


class ReservationUnavailableError(ReservationRuntimeError):
    pass


class ReservationConflictError(ReservationRuntimeError):
    pass


@dataclass(frozen=True)
class ReservationRequest:
    reservation_id: str
    idempotency_key: str
    store_id: str
    warehouse_id: str
    product_id: str
    quantity: int
    partial_policy: Literal["reject_partial", "allow_partial"] = "reject_partial"


@dataclass(frozen=True)
class ReservationResult:
    reservation_id: str
    tenant_id: str
    product_id: str
    warehouse_id: str
    quantity: int
    status: str


class ReservationRuntimeClient:
    """Authorized, restart-safe client for the Rust reservation runtime."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder, executable: str, availability: tuple[dict[str, object], ...], journal_path: Path, timeout_seconds: float = 2.0) -> None:
        self._authorization = authorization
        self._audit = audit
        self._executable = executable
        self._availability = availability
        self._journal_path = journal_path
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._process: subprocess.Popen[str] | None = None

    def reserve(self, principal_id: str, session_id: str, scope: ScopeContext, request: ReservationRequest, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.write", request.store_id)
        return self._reservation({"type": "reserve", "tenant_id": scope.tenant_id, **asdict(request)}, scope, trace_id, principal_id)

    def confirm(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.write")
        return self._reservation({"type": "confirm", "reservation_id": reservation_id}, scope, trace_id, principal_id)

    def expire(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.write")
        return self._reservation({"type": "expire", "reservation_id": reservation_id}, scope, trace_id, principal_id)

    def release(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.write")
        return self._reservation({"type": "release", "reservation_id": reservation_id}, scope, trace_id, principal_id)

    def status(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.read")
        return self._result(self._send({"type": "status", "reservation_id": reservation_id}), scope, trace_id, principal_id)

    def availability(self, principal_id: str, session_id: str, scope: ScopeContext, warehouse_id: str, product_id: str, trace_id: str) -> int:
        self._authorize(principal_id, session_id, scope, "reservation.read", warehouse_id)
        response = self._send({"type": "availability", "tenant_id": scope.tenant_id, "warehouse_id": warehouse_id, "product_id": product_id})
        result = response.get("result", {})
        if result.get("type") != "availability":
            self._raise(response)
        self._record(principal_id, "reservation.availability", product_id, trace_id)
        return int(result["quantity"])

    def _reservation(self, payload: dict[str, object], scope: ScopeContext, trace_id: str, principal_id: str) -> ReservationResult:
        result = self._result(self._send(payload), scope, trace_id, principal_id)
        self._append(payload)
        return result

    def _result(self, response: dict[str, object], scope: ScopeContext, trace_id: str, principal_id: str) -> ReservationResult:
        result = response.get("result", {})
        if not isinstance(result, dict) or result.get("type") != "reservation":
            self._raise(response)
        reservation = result["reservation"]
        if not isinstance(reservation, dict) or reservation.get("tenant_id") != scope.tenant_id:
            raise ReservationRuntimeError("reservation response is outside tenant scope")
        value = ReservationResult(**reservation)
        self._record(principal_id, f"reservation.{value.status}", value.reservation_id, trace_id)
        return value

    def _send(self, command: dict[str, object], retry: bool = True) -> dict[str, object]:
        with self._lock:
            try:
                process = self._ensure_process()
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(json.dumps({"version": "v1", "command": command}) + "\n")
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise ReservationUnavailableError("reservation runtime ended without a response")
                response = json.loads(line)
                if response.get("version") != "v1":
                    raise ReservationRuntimeError("reservation runtime returned an incompatible contract version")
                return response
            except (OSError, json.JSONDecodeError, ReservationUnavailableError) as error:
                self._stop()
                if retry:
                    return self._send(command, retry=False)
                raise ReservationUnavailableError("reservation runtime is unavailable after retry") from error

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._process = subprocess.Popen([self._executable], text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._send_initialize()
        self._replay()
        return self._process

    def _send_initialize(self) -> None:
        assert self._process is not None and self._process.stdin is not None and self._process.stdout is not None
        self._process.stdin.write(json.dumps({"version": "v1", "command": {"type": "initialize", "availability": self._availability}}) + "\n")
        self._process.stdin.flush()
        if not self._process.stdout.readline():
            raise ReservationUnavailableError("reservation runtime did not initialize")

    def _replay(self) -> None:
        if not self._journal_path.exists():
            return
        for line in self._journal_path.read_text().splitlines():
            if line:
                self._send(json.loads(line), retry=False)

    def _append(self, payload: dict[str, object]) -> None:
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._journal_path.open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(payload, sort_keys=True) + "\n")
            journal.flush()

    def _stop(self) -> None:
        if self._process is not None:
            self._process.kill()
        self._process = None

    def _raise(self, response: dict[str, object]) -> None:
        result = response.get("result", {})
        if isinstance(result, dict) and result.get("type") == "conflict":
            raise ReservationConflictError(f"insufficient stock: {result.get('available_quantity')}")
        if isinstance(result, dict) and result.get("retryable"):
            raise ReservationUnavailableError(str(result.get("message")))
        raise ReservationRuntimeError(str(result.get("message", "reservation request failed")))

    def _authorize(self, principal_id: str, session_id: str, scope: ScopeContext, action: str, organization_id: str | None = None) -> None:
        self._authorization.authorize(principal_id, session_id, scope, action, organization_id=organization_id)

    def _record(self, actor_id: str, source: str, subject_id: str, trace_id: str) -> None:
        self._audit.record(actor_id, source, "reservation-runtime", subject_id, "v1", trace_id, "allowed")
