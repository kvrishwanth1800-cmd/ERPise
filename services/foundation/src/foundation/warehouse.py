# ruff: noqa: E501
"""Warehouse task, scan, FEFO, shipment, and transfer lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.inventory import InventoryLedger, InventoryMovement
from foundation.organization import ScopeContext


class WarehouseValidationError(ValueError):
    """Raised when warehouse work violates its lifecycle or stock policy."""


@dataclass(frozen=True)
class StockLot:
    product_id: str
    warehouse_id: str
    bin_id: str
    quantity: Decimal
    batch_id: str | None = None
    expires_on: date | None = None


@dataclass(frozen=True)
class TaskLine:
    line_id: str
    product_id: str
    quantity: Decimal
    source_bin_id: str
    destination_bin_id: str | None = None
    allow_substitution: bool = False


@dataclass(frozen=True)
class TaskException:
    exception_id: str
    line_id: str
    kind: str
    quantity: Decimal
    reason: str
    substitute_product_id: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class WarehouseTask:
    task_id: str
    tenant_id: str
    warehouse_id: str
    task_type: str
    lines: tuple[TaskLine, ...]
    status: str = "open"
    assignee_id: str | None = None
    claimed_by: str | None = None
    scanned_line_ids: tuple[str, ...] = ()
    exceptions: tuple[TaskException, ...] = ()


@dataclass(frozen=True)
class TransferLine:
    line_id: str
    product_id: str
    quantity: Decimal
    sent_quantity: Decimal = Decimal()
    received_quantity: Decimal = Decimal()


@dataclass(frozen=True)
class WarehouseTransfer:
    transfer_id: str
    tenant_id: str
    source_warehouse_id: str
    source_bin_id: str
    destination_warehouse_id: str
    destination_bin_id: str
    lines: tuple[TransferLine, ...]
    status: str = "draft"
    discrepancy_reason: str | None = None


@dataclass(frozen=True)
class WarehouseEvent:
    event_type: str
    tenant_id: str
    aggregate_id: str


class WarehouseService:
    """Enforces accountable warehouse work and posts stock movement facts."""

    _task_types = frozenset({"putaway", "picking", "packing", "internal_movement"})
    _exception_types = frozenset({"short", "substitute"})

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder, ledger: InventoryLedger) -> None:
        self._authorization = authorization
        self._audit = audit
        self._ledger = ledger
        self._tasks: dict[tuple[str, str], WarehouseTask] = {}
        self._transfers: dict[tuple[str, str], WarehouseTransfer] = {}
        self.outbox: list[WarehouseEvent] = []

    def create_task(self, principal_id: str, session_id: str, scope: ScopeContext, task: WarehouseTask, trace_id: str) -> WarehouseTask:
        self._authorize(principal_id, session_id, scope)
        self._validate_task(task, scope)
        key = (scope.tenant_id, task.task_id)
        if key in self._tasks:
            raise WarehouseValidationError("task id already exists in this tenant")
        self._tasks[key] = task
        self._record(principal_id, "task.create", task.task_id, trace_id, scope.tenant_id, "WarehouseTaskChanged")
        return task

    def assign(self, principal_id: str, session_id: str, scope: ScopeContext, task_id: str, assignee_id: str, trace_id: str) -> WarehouseTask:
        task = self._task(scope, task_id)
        self._authorize(principal_id, session_id, scope)
        if task.status not in {"open", "assigned"} or not assignee_id:
            raise WarehouseValidationError("only open or assigned tasks can be assigned")
        return self._save_task(replace(task, status="assigned", assignee_id=assignee_id, claimed_by=None), principal_id, "task.assign", trace_id)

    def claim(self, principal_id: str, session_id: str, scope: ScopeContext, task_id: str, trace_id: str) -> WarehouseTask:
        task = self._task(scope, task_id)
        self._authorize(principal_id, session_id, scope)
        if task.status not in {"open", "assigned"}:
            raise WarehouseValidationError("task cannot be claimed in its current state")
        if task.assignee_id not in {None, principal_id}:
            raise WarehouseValidationError("task is assigned to another worker")
        return self._save_task(replace(task, status="in_progress", assignee_id=principal_id, claimed_by=principal_id), principal_id, "task.claim", trace_id)

    def scan(self, principal_id: str, session_id: str, scope: ScopeContext, task_id: str, line_id: str, product_id: str, bin_id: str, lots: tuple[StockLot, ...], trace_id: str) -> StockLot:
        task = self._task(scope, task_id)
        self._authorize(principal_id, session_id, scope)
        self._require_claim(task, principal_id)
        line = self._line(task, line_id)
        if line.product_id != product_id or line.source_bin_id != bin_id:
            raise WarehouseValidationError("scan does not match the task line")
        eligible = tuple(lot for lot in lots if lot.warehouse_id == task.warehouse_id and lot.bin_id == bin_id and lot.product_id == product_id and lot.quantity > 0)
        if not eligible:
            raise WarehouseValidationError("scan has no eligible stock in the task warehouse and bin")
        selected = self.select_fefo(eligible)
        if selected.quantity < line.quantity:
            raise WarehouseValidationError("selected stock does not satisfy the task quantity")
        if line_id not in task.scanned_line_ids:
            task = replace(task, scanned_line_ids=(*task.scanned_line_ids, line_id))
            self._save_task(task, principal_id, "task.scan", trace_id)
        return selected

    def record_exception(self, principal_id: str, session_id: str, scope: ScopeContext, task_id: str, exception: TaskException, trace_id: str) -> WarehouseTask:
        task = self._task(scope, task_id)
        self._authorize(principal_id, session_id, scope)
        self._require_claim(task, principal_id)
        line = self._line(task, exception.line_id)
        if exception.kind not in self._exception_types or exception.quantity <= 0 or not exception.reason:
            raise WarehouseValidationError("a valid short or substitute exception is required")
        if exception.kind == "substitute" and (not line.allow_substitution or not exception.substitute_product_id):
            raise WarehouseValidationError("substitution is not allowed for this task line")
        if exception.kind == "short" and exception.quantity > line.quantity:
            raise WarehouseValidationError("short quantity exceeds the task quantity")
        if exception.occurred_at.tzinfo is None:
            raise WarehouseValidationError("exception timestamp must be timezone-aware")
        if any(item.exception_id == exception.exception_id for item in task.exceptions):
            return task
        return self._save_task(replace(task, exceptions=(*task.exceptions, exception)), principal_id, "task.exception", trace_id)

    def complete_task(self, principal_id: str, session_id: str, scope: ScopeContext, task_id: str, occurred_at: datetime, trace_id: str) -> WarehouseTask:
        task = self._task(scope, task_id)
        self._authorize(principal_id, session_id, scope)
        self._require_claim(task, principal_id)
        covered = set(task.scanned_line_ids) | {item.line_id for item in task.exceptions}
        if covered != {line.line_id for line in task.lines}:
            raise WarehouseValidationError("every task line needs a valid scan or recorded exception")
        if occurred_at.tzinfo is None:
            raise WarehouseValidationError("completion timestamp must be timezone-aware")
        if task.task_type in {"putaway", "internal_movement"}:
            self._post_task_movements(principal_id, session_id, scope, task, occurred_at, trace_id)
        return self._save_task(replace(task, status="completed"), principal_id, "task.complete", trace_id)

    def cancel_task(self, principal_id: str, session_id: str, scope: ScopeContext, task_id: str, reason: str, trace_id: str) -> WarehouseTask:
        task = self._task(scope, task_id)
        self._authorize(principal_id, session_id, scope)
        if task.status in {"completed", "cancelled"} or not reason:
            raise WarehouseValidationError("task cannot be cancelled")
        return self._save_task(replace(task, status="cancelled"), principal_id, "task.cancel", trace_id, reason)

    def shipment_ready(self, scope: ScopeContext, task_ids: tuple[str, ...]) -> bool:
        tasks = tuple(self._task(scope, task_id) for task_id in task_ids)
        return bool(tasks) and all(task.task_type in {"picking", "packing"} and task.status == "completed" for task in tasks)

    def create_transfer(self, principal_id: str, session_id: str, scope: ScopeContext, transfer: WarehouseTransfer, trace_id: str) -> WarehouseTransfer:
        self._authorize(principal_id, session_id, scope)
        if transfer.tenant_id != scope.tenant_id or not transfer.lines or transfer.source_warehouse_id == transfer.destination_warehouse_id and transfer.source_bin_id == transfer.destination_bin_id:
            raise WarehouseValidationError("transfer scope, route, or lines are invalid")
        if any(line.quantity <= 0 for line in transfer.lines):
            raise WarehouseValidationError("transfer quantities must be positive")
        key = (scope.tenant_id, transfer.transfer_id)
        if key in self._transfers:
            raise WarehouseValidationError("transfer id already exists in this tenant")
        self._transfers[key] = transfer
        self._record(principal_id, "transfer.create", transfer.transfer_id, trace_id, scope.tenant_id, "TransferChanged")
        return transfer

    def send_transfer(self, principal_id: str, session_id: str, scope: ScopeContext, transfer_id: str, quantities: dict[str, Decimal], occurred_at: datetime, trace_id: str) -> WarehouseTransfer:
        transfer = self._transfer(scope, transfer_id)
        self._authorize(principal_id, session_id, scope)
        if transfer.status != "draft" or occurred_at.tzinfo is None:
            raise WarehouseValidationError("only a draft transfer can be sent")
        lines = self._apply_quantities(transfer.lines, quantities, "send")
        for line in lines:
            if line.sent_quantity:
                self._ledger.post(principal_id, session_id, scope, InventoryMovement(f"transfer-send-{transfer_id}-{line.line_id}", line.product_id, transfer.source_bin_id, "physical", -line.sent_quantity, "warehouse transfer sent", occurred_at), trace_id)
                self._ledger.post(principal_id, session_id, scope, InventoryMovement(f"transfer-transit-{transfer_id}-{line.line_id}", line.product_id, transfer.destination_warehouse_id, "in_transit", line.sent_quantity, "warehouse transfer sent", occurred_at), trace_id)
        return self._save_transfer(replace(transfer, lines=lines, status="in_transit"), principal_id, "transfer.send", trace_id)

    def receive_transfer(self, principal_id: str, session_id: str, scope: ScopeContext, transfer_id: str, quantities: dict[str, Decimal], occurred_at: datetime, trace_id: str, discrepancy_reason: str | None = None) -> WarehouseTransfer:
        transfer = self._transfer(scope, transfer_id)
        self._authorize(principal_id, session_id, scope)
        if transfer.status not in {"in_transit", "partially_received"} or occurred_at.tzinfo is None:
            raise WarehouseValidationError("transfer is not available for receipt")
        lines = self._apply_quantities(transfer.lines, quantities, "receive")
        received_now = {line_id: quantity for line_id, quantity in quantities.items() if quantity > 0}
        for line in lines:
            quantity = received_now.get(line.line_id, Decimal())
            if quantity:
                self._ledger.post(principal_id, session_id, scope, InventoryMovement(f"transfer-receive-{transfer_id}-{line.line_id}-{line.received_quantity}", line.product_id, transfer.destination_bin_id, "physical", quantity, "warehouse transfer received", occurred_at), trace_id)
                self._ledger.post(principal_id, session_id, scope, InventoryMovement(f"transfer-transit-receive-{transfer_id}-{line.line_id}-{line.received_quantity}", line.product_id, transfer.destination_warehouse_id, "in_transit", -quantity, "warehouse transfer received", occurred_at), trace_id)
        complete = all(line.received_quantity == line.sent_quantity for line in lines)
        discrepancy = any(line.received_quantity != line.sent_quantity for line in lines)
        if complete:
            status = "received"
        else:
            status = "partially_received"
        if discrepancy and not discrepancy_reason and all(line.received_quantity >= line.sent_quantity for line in lines):
            raise WarehouseValidationError("a discrepancy reason is required for a mismatched final receipt")
        return self._save_transfer(replace(transfer, lines=lines, status=status, discrepancy_reason=discrepancy_reason), principal_id, "transfer.receive", trace_id)

    @staticmethod
    def select_fefo(lots: tuple[StockLot, ...]) -> StockLot:
        if not lots:
            raise WarehouseValidationError("FEFO selection requires eligible stock")
        dated = tuple(lot for lot in lots if lot.expires_on is not None)
        return min(dated, key=lambda lot: (lot.expires_on, lot.batch_id or "")) if dated else min(lots, key=lambda lot: (lot.batch_id or "", lot.bin_id))

    def task(self, scope: ScopeContext, task_id: str) -> WarehouseTask | None:
        return self._tasks.get((scope.tenant_id, task_id))

    def transfer(self, scope: ScopeContext, transfer_id: str) -> WarehouseTransfer | None:
        return self._transfers.get((scope.tenant_id, transfer_id))

    def _authorize(self, principal_id: str, session_id: str, scope: ScopeContext) -> None:
        self._authorization.authorize(principal_id, session_id, scope, "warehouse.write")

    def _task(self, scope: ScopeContext, task_id: str) -> WarehouseTask:
        task = self.task(scope, task_id)
        if task is None:
            raise WarehouseValidationError("task is unknown in this tenant")
        return task

    def _transfer(self, scope: ScopeContext, transfer_id: str) -> WarehouseTransfer:
        transfer = self.transfer(scope, transfer_id)
        if transfer is None:
            raise WarehouseValidationError("transfer is unknown in this tenant")
        return transfer

    @staticmethod
    def _line(task: WarehouseTask, line_id: str) -> TaskLine:
        for line in task.lines:
            if line.line_id == line_id:
                return line
        raise WarehouseValidationError("scan or exception does not match a task line")

    @staticmethod
    def _require_claim(task: WarehouseTask, principal_id: str) -> None:
        if task.status != "in_progress" or task.claimed_by != principal_id:
            raise WarehouseValidationError("warehouse action requires a task claimed by this worker")

    @classmethod
    def _validate_task(cls, task: WarehouseTask, scope: ScopeContext) -> None:
        if task.tenant_id != scope.tenant_id or task.task_type not in cls._task_types or not task.lines or not task.warehouse_id:
            raise WarehouseValidationError("task scope, type, warehouse, and lines are required")
        if any(line.quantity <= 0 or not line.source_bin_id for line in task.lines):
            raise WarehouseValidationError("task lines need positive quantities and source bins")

    def _save_task(self, task: WarehouseTask, principal_id: str, action: str, trace_id: str, reason: str = "warehouse task lifecycle") -> WarehouseTask:
        self._tasks[(task.tenant_id, task.task_id)] = task
        self._record(principal_id, action, task.task_id, trace_id, task.tenant_id, "WarehouseTaskChanged", reason)
        return task

    def _save_transfer(self, transfer: WarehouseTransfer, principal_id: str, action: str, trace_id: str) -> WarehouseTransfer:
        self._transfers[(transfer.tenant_id, transfer.transfer_id)] = transfer
        self._record(principal_id, action, transfer.transfer_id, trace_id, transfer.tenant_id, "TransferChanged")
        return transfer

    def _record(self, principal_id: str, action: str, aggregate_id: str, trace_id: str, tenant_id: str, event_type: str, reason: str = "warehouse lifecycle") -> None:
        self._audit.record(principal_id, "warehouse.write", action, reason, "v1", trace_id, "allowed")
        self.outbox.append(WarehouseEvent(event_type, tenant_id, aggregate_id))

    def _post_task_movements(self, principal_id: str, session_id: str, scope: ScopeContext, task: WarehouseTask, occurred_at: datetime, trace_id: str) -> None:
        for line in task.lines:
            if line.line_id not in task.scanned_line_ids or line.destination_bin_id is None:
                continue
            self._ledger.post(principal_id, session_id, scope, InventoryMovement(f"task-source-{task.task_id}-{line.line_id}", line.product_id, line.source_bin_id, "physical", -line.quantity, "warehouse task completed", occurred_at), trace_id)
            self._ledger.post(principal_id, session_id, scope, InventoryMovement(f"task-destination-{task.task_id}-{line.line_id}", line.product_id, line.destination_bin_id, "physical", line.quantity, "warehouse task completed", occurred_at), trace_id)

    @staticmethod
    def _apply_quantities(lines: tuple[TransferLine, ...], quantities: dict[str, Decimal], operation: str) -> tuple[TransferLine, ...]:
        known = {line.line_id for line in lines}
        if not quantities or set(quantities) - known or any(quantity < 0 for quantity in quantities.values()):
            raise WarehouseValidationError("transfer quantities do not match its lines")
        result: list[TransferLine] = []
        for line in lines:
            quantity = quantities.get(line.line_id, Decimal())
            if operation == "send":
                if quantity > line.quantity:
                    raise WarehouseValidationError("sent quantity exceeds requested quantity")
                result.append(replace(line, sent_quantity=quantity))
            else:
                received = line.received_quantity + quantity
                if received > line.sent_quantity:
                    raise WarehouseValidationError("received quantity exceeds sent quantity")
                result.append(replace(line, received_quantity=received))
        return tuple(result)
