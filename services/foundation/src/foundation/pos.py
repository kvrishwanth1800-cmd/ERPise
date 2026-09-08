"""Tenant-scoped counter POS orchestration: shifts, sales, returns, and offline-safe replay.

POS composes the shared pricing, inventory, and payment truth (ADR-001: POS composes
shared truth); it does not maintain an independent price list, stock ledger, or payment
state machine. Sale and return facts here reference the quote, inventory movement, and
payment transaction identifiers produced by those services so that one committed sale or
return always resolves to exactly one linked stock, payment, receipt, and audit outcome.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class PosValidationError(ValueError):
    """Raised when a POS command cannot preserve the required sale, shift, or return evidence."""


class ShiftStateError(PosValidationError):
    """Raised when a shift command is invalid for the shift's current lifecycle state."""


class ReturnAuthorityError(PosValidationError):
    """Raised when a return is recorded without a prior granted return authorization."""


@dataclass(frozen=True)
class RegisterShift:
    shift_id: str
    tenant_id: str
    store_id: str
    register_id: str
    opened_by: str
    opening_float: Decimal
    opened_at: datetime
    status: str = "open"
    declared_cash: Decimal | None = None
    recorded_cash: Decimal | None = None
    variance: Decimal | None = None
    closed_by: str | None = None
    closed_at: datetime | None = None


@dataclass(frozen=True)
class SaleLine:
    product_id: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class Sale:
    sale_id: str
    tenant_id: str
    shift_id: str
    idempotency_key: str
    lines: tuple[SaleLine, ...]
    total_amount: Decimal
    currency: str
    tender_type: str
    payment_transaction_id: str
    inventory_movement_ids: tuple[str, ...]
    receipt_id: str
    connectivity_state: str
    sold_at: datetime


@dataclass(frozen=True)
class ReturnAuthorization:
    authorization_id: str
    tenant_id: str
    sale_id: str
    authorized_by: str
    granted_at: datetime


@dataclass(frozen=True)
class Return:
    return_id: str
    tenant_id: str
    original_sale_id: str
    idempotency_key: str
    lines: tuple[SaleLine, ...]
    total_amount: Decimal
    tender_type: str
    refund_transaction_id: str
    inventory_movement_ids: tuple[str, ...]
    reason: str
    returned_at: datetime


@dataclass(frozen=True)
class ConnectivityAdvisory:
    tenant_id: str
    register_id: str
    state: str
    changed_at: datetime


@dataclass(frozen=True)
class PosEvent:
    event_type: str
    tenant_id: str
    subject_id: str


_CONNECTIVITY_STATES = frozenset({"online", "offline"})
_TENDER_TYPES = frozenset({"cash", "card", "other"})


class PosOperationsService:
    """Runs counter POS shift lifecycle, sale and return recording, and offline-safe replay."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._shifts: dict[tuple[str, str], RegisterShift] = {}
        self._sales: dict[tuple[str, str], Sale] = {}
        self._sales_by_key: dict[tuple[str, str], Sale] = {}
        self._returns: dict[tuple[str, str], Return] = {}
        self._returns_by_key: dict[tuple[str, str], Return] = {}
        self._return_authorizations: dict[tuple[str, str], ReturnAuthorization] = {}
        self._connectivity: dict[tuple[str, str], str] = {}
        self.outbox: list[PosEvent] = []

    def open_shift(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        shift: RegisterShift,
        trace_id: str,
    ) -> RegisterShift:
        self._authorize(principal_id, session_id, scope, "pos.shift.write")
        if (
            not all((shift.shift_id, shift.store_id, shift.register_id, shift.opened_by))
            or shift.opening_float < 0
            or shift.opened_at.tzinfo is None
        ):
            raise PosValidationError(
                "shifts require identity, a non-negative opening float, and timezone-aware time"
            )
        if shift.status != "open" or shift.declared_cash is not None or shift.closed_at is not None:
            raise PosValidationError("a newly opened shift must start in the open state")
        key = (scope.tenant_id, shift.shift_id)
        if key in self._shifts:
            raise PosValidationError("shift identifiers are immutable")
        self._shifts[key] = shift
        self._record(principal_id, "pos.shift.open", shift.shift_id, trace_id)
        self.outbox.append(PosEvent("ShiftOpened", scope.tenant_id, shift.shift_id))
        return shift

    def record_sale(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        sale: Sale,
        trace_id: str,
    ) -> Sale:
        self._authorize(principal_id, session_id, scope, "pos.sale.write")
        shift = self._shift(scope, sale.shift_id)
        if shift.status != "open":
            raise ShiftStateError("sales require an open shift")
        if (
            not all(
                (
                    sale.sale_id,
                    sale.idempotency_key,
                    sale.currency,
                    sale.payment_transaction_id,
                    sale.receipt_id,
                )
            )
            or not sale.lines
            or any(line.quantity <= 0 or line.unit_price < 0 for line in sale.lines)
            or sale.total_amount != sum((line.line_total for line in sale.lines), Decimal())
            or sale.tender_type not in _TENDER_TYPES
            or sale.connectivity_state not in _CONNECTIVITY_STATES
            or sale.sold_at.tzinfo is None
        ):
            raise PosValidationError(
                "sales require identity, a verified payment and receipt reference, at least "
                "one positive line, a total matching the lines, a known tender type, and "
                "timezone-aware time"
            )
        key = (scope.tenant_id, sale.idempotency_key)
        existing = self._sales_by_key.get(key)
        if existing is not None:
            return existing
        sale_key = (scope.tenant_id, sale.sale_id)
        if sale_key in self._sales:
            raise PosValidationError("sale identifiers are immutable")
        self._sales[sale_key] = sale
        self._sales_by_key[key] = sale
        self._record(principal_id, "pos.sale.record", sale.sale_id, trace_id)
        self.outbox.append(PosEvent("SaleCompleted", scope.tenant_id, sale.sale_id))
        return sale

    def replay_offline_sales(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        sales: Sequence[Sale],
        trace_id: str,
    ) -> tuple[Sale, ...]:
        """Re-applies a queued offline batch; duplicate idempotency keys resolve to the same fact."""
        return tuple(
            self.record_sale(principal_id, session_id, scope, sale, trace_id) for sale in sales
        )

    def authorize_return(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        authorization: ReturnAuthorization,
        trace_id: str,
    ) -> ReturnAuthorization:
        self._authorize(principal_id, session_id, scope, "pos.return.authorize")
        self._sale(scope, authorization.sale_id)
        if (
            not all(
                (authorization.authorization_id, authorization.sale_id, authorization.authorized_by)
            )
            or authorization.granted_at.tzinfo is None
        ):
            raise PosValidationError(
                "return authorizations require identity, a sale reference, and timezone-aware time"
            )
        key = (scope.tenant_id, authorization.sale_id)
        self._return_authorizations[key] = authorization
        self._record(principal_id, "pos.return.authorize", authorization.authorization_id, trace_id)
        self.outbox.append(
            PosEvent("ReturnAuthorized", scope.tenant_id, authorization.authorization_id)
        )
        return authorization

    def record_return(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        return_: Return,
        trace_id: str,
    ) -> Return:
        self._authorize(principal_id, session_id, scope, "pos.return.write")
        self._sale(scope, return_.original_sale_id)
        authorization = self._return_authorizations.get((scope.tenant_id, return_.original_sale_id))
        if authorization is None:
            raise ReturnAuthorityError(
                "a return authorization must be granted before corrective effects are recorded"
            )
        if (
            not all(
                (
                    return_.return_id,
                    return_.idempotency_key,
                    return_.refund_transaction_id,
                    return_.reason,
                )
            )
            or not return_.lines
            or any(line.quantity <= 0 or line.unit_price < 0 for line in return_.lines)
            or return_.total_amount != sum((line.line_total for line in return_.lines), Decimal())
            or return_.tender_type not in _TENDER_TYPES
            or return_.returned_at.tzinfo is None
        ):
            raise PosValidationError(
                "returns require identity, a verified refund reference, a reason, at least one "
                "positive line, a total matching the lines, a known tender type, and "
                "timezone-aware time"
            )
        key = (scope.tenant_id, return_.idempotency_key)
        existing = self._returns_by_key.get(key)
        if existing is not None:
            return existing
        return_key = (scope.tenant_id, return_.return_id)
        if return_key in self._returns:
            raise PosValidationError("return identifiers are immutable")
        self._returns[return_key] = return_
        self._returns_by_key[key] = return_
        self._record(principal_id, "pos.return.record", return_.return_id, trace_id)
        self.outbox.append(PosEvent("ReturnCompleted", scope.tenant_id, return_.return_id))
        return return_

    def close_shift(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        shift_id: str,
        declared_cash: Decimal,
        closed_by: str,
        closed_at: datetime,
        trace_id: str,
    ) -> RegisterShift:
        self._authorize(principal_id, session_id, scope, "pos.shift.write")
        shift = self._shift(scope, shift_id)
        recorded_cash = self._recorded_cash(scope, shift)
        variance = declared_cash - recorded_cash
        if shift.status == "closed":
            if shift.declared_cash == declared_cash and shift.recorded_cash == recorded_cash:
                return shift
            raise ShiftStateError("a closed shift's reconciliation is immutable")
        if declared_cash < 0 or not closed_by or closed_at.tzinfo is None:
            raise PosValidationError(
                "closing a shift requires a non-negative declared cash amount, a closer, and "
                "timezone-aware time"
            )
        closed = RegisterShift(
            shift_id=shift.shift_id,
            tenant_id=shift.tenant_id,
            store_id=shift.store_id,
            register_id=shift.register_id,
            opened_by=shift.opened_by,
            opening_float=shift.opening_float,
            opened_at=shift.opened_at,
            status="closed",
            declared_cash=declared_cash,
            recorded_cash=recorded_cash,
            variance=variance,
            closed_by=closed_by,
            closed_at=closed_at,
        )
        self._shifts[(scope.tenant_id, shift_id)] = closed
        self._record(principal_id, "pos.shift.close", shift_id, trace_id)
        self.outbox.append(PosEvent("ShiftClosed", scope.tenant_id, shift_id))
        if variance != 0:
            self.outbox.append(PosEvent("ShiftVarianceReported", scope.tenant_id, shift_id))
        return closed

    def set_connectivity_state(
        self,
        scope: ScopeContext,
        register_id: str,
        state: str,
        changed_at: datetime,
        trace_id: str,
    ) -> ConnectivityAdvisory:
        if state not in _CONNECTIVITY_STATES or not register_id or changed_at.tzinfo is None:
            raise PosValidationError(
                "connectivity advisories require a known state, a register, and timezone-aware time"
            )
        self._connectivity[(scope.tenant_id, register_id)] = state
        advisory = ConnectivityAdvisory(scope.tenant_id, register_id, state, changed_at)
        self.outbox.append(PosEvent("RegisterConnectivityChanged", scope.tenant_id, register_id))
        return advisory

    def connectivity_state(self, scope: ScopeContext, register_id: str) -> str:
        return self._connectivity.get((scope.tenant_id, register_id), "online")

    def sales_for_shift(self, scope: ScopeContext, shift_id: str) -> tuple[Sale, ...]:
        self._shift(scope, shift_id)
        return tuple(
            sale
            for (tenant_id, _), sale in self._sales.items()
            if tenant_id == scope.tenant_id and sale.shift_id == shift_id
        )

    def returns_for_sale(self, scope: ScopeContext, sale_id: str) -> tuple[Return, ...]:
        self._sale(scope, sale_id)
        return tuple(
            return_
            for (tenant_id, _), return_ in self._returns.items()
            if tenant_id == scope.tenant_id and return_.original_sale_id == sale_id
        )

    def _recorded_cash(self, scope: ScopeContext, shift: RegisterShift) -> Decimal:
        cash_sales = sum(
            (
                sale.total_amount
                for sale in self.sales_for_shift(scope, shift.shift_id)
                if sale.tender_type == "cash"
            ),
            Decimal(),
        )
        cash_returns = sum(
            (
                return_.total_amount
                for sale in self.sales_for_shift(scope, shift.shift_id)
                for return_ in self.returns_for_sale(scope, sale.sale_id)
                if return_.tender_type == "cash"
            ),
            Decimal(),
        )
        return shift.opening_float + cash_sales - cash_returns

    def _shift(self, scope: ScopeContext, shift_id: str) -> RegisterShift:
        shift = self._shifts.get((scope.tenant_id, shift_id))
        if shift is None:
            raise PosValidationError("shift is outside tenant scope or unknown")
        return shift

    def _sale(self, scope: ScopeContext, sale_id: str) -> Sale:
        sale = self._sales.get((scope.tenant_id, sale_id))
        if sale is None:
            raise PosValidationError("sale is outside tenant scope or unknown")
        return sale

    def _authorize(
        self, principal_id: str, session_id: str, scope: ScopeContext, action: str
    ) -> None:
        self._authorization.authorize(principal_id, session_id, scope, action)

    def _record(self, actor_id: str, source: str, subject: str, trace_id: str) -> None:
        self._audit.record(actor_id, source, "pos-service", subject, "v1", trace_id, "allowed")
