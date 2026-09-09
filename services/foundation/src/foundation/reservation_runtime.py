"""Authorized subprocess boundary for the Rust durable reservation authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
import queue
import subprocess
from threading import Lock, Thread
from typing import Literal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class ReservationRuntimeError(RuntimeError):
    """The reservation authority rejected a command."""


class ReservationUnavailableError(ReservationRuntimeError):
    """The reservation authority cannot be reached safely."""


class ReservationConflictError(ReservationRuntimeError):
    """The requested stock cannot be reserved."""


@dataclass(frozen=True)
class ReservationRequest:
    reservation_id: str
    idempotency_key: str
    store_id: str
    warehouse_id: str
    product_id: str
    quantity: int
    expires_at: datetime


@dataclass(frozen=True)
class ReservationResult:
    reservation_id: str
    tenant_id: str
    store_id: str
    warehouse_id: str
    product_id: str
    quantity: int
    status: str
    expires_at: datetime


class ReservationRuntimeClient:
    """Calls Rust without holding reservation state or replay journals in Python."""

    def __init__(
        self,
        authorization: AuthorizationService,
        audit: AuditRecorder,
        executable: str,
        availability: tuple[dict[str, object], ...],
        database_url: str,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._authorization = authorization
        self._audit = audit
        self._executable = executable
        self._availability = availability
        self._database_url = database_url
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._process: subprocess.Popen[str] | None = None

    def reserve(
        self, principal_id: str, session_id: str, scope: ScopeContext,
        request: ReservationRequest, trace_id: str,
    ) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.write", request.store_id)
        payload = asdict(request)
        payload["expires_at"] = request.expires_at.astimezone(UTC).isoformat()
        return self._result(self._request({"type": "reserve", "tenant_id": scope.tenant_id, "trace_id": trace_id, **payload}), scope, principal_id, trace_id)

    def confirm(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        return self._transition("confirm", principal_id, session_id, scope, reservation_id, trace_id)

    def release(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        return self._transition("release", principal_id, session_id, scope, reservation_id, trace_id)

    def expire(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        return self._transition("expire", principal_id, session_id, scope, reservation_id, trace_id)

    def status(self, principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.read")
        return self._result(self._request({"type": "status", "tenant_id": scope.tenant_id, "reservation_id": reservation_id}), scope, principal_id, trace_id)

    def availability(self, principal_id: str, session_id: str, scope: ScopeContext, store_id: str, warehouse_id: str, product_id: str, trace_id: str) -> int:
        self._authorize(principal_id, session_id, scope, "reservation.read", store_id)
        response = self._request({"type": "availability", "tenant_id": scope.tenant_id, "store_id": store_id, "warehouse_id": warehouse_id, "product_id": product_id})
        result = response.get("result", {})
        if result.get("type") != "availability":
            self._raise(response)
        self._record(principal_id, "reservation.availability", product_id, trace_id)
        return int(result["quantity"])

    def expire_due(self, trace_id: str) -> tuple[str, ...]:
        response = self._request({"type": "expire_due", "trace_id": trace_id})
        result = response.get("result", {})
        if result.get("type") != "expired":
            self._raise(response)
        return tuple(str(value) for value in result["reservation_ids"])

    def _transition(self, action: Literal["confirm", "release", "expire"], principal_id: str, session_id: str, scope: ScopeContext, reservation_id: str, trace_id: str) -> ReservationResult:
        self._authorize(principal_id, session_id, scope, "reservation.write")
        return self._result(self._request({"type": action, "tenant_id": scope.tenant_id, "reservation_id": reservation_id, "trace_id": trace_id}), scope, principal_id, trace_id)

    def _result(self, response: dict[str, object], scope: ScopeContext, principal_id: str, trace_id: str) -> ReservationResult:
        result = response.get("result", {})
        if not isinstance(result, dict) or result.get("type") != "reservation":
            self._raise(response)
        value = result["reservation"]
        if not isinstance(value, dict) or value.get("tenant_id") != scope.tenant_id:
            raise ReservationRuntimeError("reservation response is outside tenant scope")
        reservation = ReservationResult(
            reservation_id=str(value["reservation_id"]), tenant_id=str(value["tenant_id"]),
            store_id=str(value["store_id"]), warehouse_id=str(value["warehouse_id"]),
            product_id=str(value["product_id"]), quantity=int(value["quantity"]),
            status=str(value["status"]), expires_at=datetime.fromisoformat(str(value["expires_at"]).replace("Z", "+00:00")),
        )
        self._record(principal_id, f"reservation.{reservation.status}", reservation.reservation_id, trace_id)
        return reservation

    def _request(self, command: dict[str, object]) -> dict[str, object]:
        payload = json.dumps({"version": "v1", "command": command}) + "\n"
        with self._lock:
            for attempt in range(2):
                try:
                    process = self._ensure_process()
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(payload)
                    process.stdin.flush()
                    line = self._readline(process)
                    response = json.loads(line)
                    if response.get("version") != "v1":
                        raise ReservationRuntimeError("reservation runtime returned an incompatible contract version")
                    return response
                except (OSError, json.JSONDecodeError, ReservationUnavailableError):
                    self._stop()
                    if attempt:
                        raise ReservationUnavailableError("reservation runtime is unavailable after retry") from None
        raise AssertionError("unreachable")

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        environment = {**os.environ, "RESERVATION_DATABASE_URL": self._database_url}
        self._process = subprocess.Popen([self._executable], text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        response = self._request_initialize()
        result = response.get("result", {})
        if not isinstance(result, dict) or result.get("type") != "availability":
            self._raise(response)
        return self._process

    def _request_initialize(self) -> dict[str, object]:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(json.dumps({"version": "v1", "command": {"type": "initialize", "availability": self._availability}}) + "\n")
        self._process.stdin.flush()
        return json.loads(self._readline(self._process))

    def _readline(self, process: subprocess.Popen[str]) -> str:
        assert process.stdout is not None
        lines: queue.Queue[str] = queue.Queue(maxsize=1)
        Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
        try:
            line = lines.get(timeout=self._timeout_seconds)
        except queue.Empty as error:
            raise ReservationUnavailableError("reservation runtime response timed out") from error
        if not line:
            raise ReservationUnavailableError("reservation runtime ended without a response")
        return line

    def _stop(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.wait(timeout=self._timeout_seconds)
        self._process = None

    @staticmethod
    def request_with_default_expiry(**kwargs: object) -> ReservationRequest:
        return ReservationRequest(expires_at=datetime.now(UTC) + timedelta(minutes=15), **kwargs)  # type: ignore[arg-type]

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
