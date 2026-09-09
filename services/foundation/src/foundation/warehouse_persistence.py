# ruff: noqa: E501, I001
"""PostgreSQL persistence for warehouse tasks and transfers."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.warehouse import TaskException, TaskLine, TransferLine, WarehouseTask, WarehouseTransfer


class DurableWarehouseStore:
    """Persists warehouse aggregates atomically with durable outbox events."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def save_task(self, task: WarehouseTask, trace_id: str) -> None:
        payload = json.dumps({"lines": [self._task_line(line) for line in task.lines], "scanned_line_ids": list(task.scanned_line_ids), "exceptions": [self._exception(item) for item in task.exceptions]})

        def write(cursor: psycopg.Cursor[Any]) -> bool:
            cursor.execute("""INSERT INTO warehouse_tasks (tenant_id, task_id, warehouse_id, task_type, status, assignee_id, claimed_by, details) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (tenant_id, task_id) DO UPDATE SET status = EXCLUDED.status, assignee_id = EXCLUDED.assignee_id, claimed_by = EXCLUDED.claimed_by, details = EXCLUDED.details, updated_at = now() WHERE warehouse_tasks.warehouse_id = EXCLUDED.warehouse_id""", (task.tenant_id, task.task_id, task.warehouse_id, task.task_type, task.status, task.assignee_id, task.claimed_by, payload))
            return cursor.rowcount > 0

        self._commit(task.tenant_id, "WarehouseTaskChanged", task.task_id, trace_id, write)

    def save_transfer(self, transfer: WarehouseTransfer, trace_id: str) -> None:
        lines = json.dumps([{"line_id": line.line_id, "product_id": line.product_id, "quantity": str(line.quantity), "sent_quantity": str(line.sent_quantity), "received_quantity": str(line.received_quantity)} for line in transfer.lines])

        def write(cursor: psycopg.Cursor[Any]) -> bool:
            cursor.execute("""INSERT INTO warehouse_transfers (tenant_id, transfer_id, source_warehouse_id, source_bin_id, destination_warehouse_id, destination_bin_id, status, lines, discrepancy_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (tenant_id, transfer_id) DO UPDATE SET status = EXCLUDED.status, lines = EXCLUDED.lines, discrepancy_reason = EXCLUDED.discrepancy_reason, updated_at = now() WHERE warehouse_transfers.source_warehouse_id = EXCLUDED.source_warehouse_id AND warehouse_transfers.destination_warehouse_id = EXCLUDED.destination_warehouse_id""", (transfer.tenant_id, transfer.transfer_id, transfer.source_warehouse_id, transfer.source_bin_id, transfer.destination_warehouse_id, transfer.destination_bin_id, transfer.status, lines, transfer.discrepancy_reason))
            return cursor.rowcount > 0

        self._commit(transfer.tenant_id, "TransferChanged", transfer.transfer_id, trace_id, write)

    def task(self, tenant_id: str, warehouse_id: str, task_id: str) -> WarehouseTask | None:
        row = self._connection.execute("SELECT task_type, status, assignee_id, claimed_by, details FROM warehouse_tasks WHERE tenant_id = %s AND warehouse_id = %s AND task_id = %s", (tenant_id, warehouse_id, task_id)).fetchone()
        if row is None:
            return None
        task_type, status, assignee_id, claimed_by, details = row
        exceptions = tuple(TaskException(item["exception_id"], item["line_id"], item["kind"], Decimal(item["quantity"]), item["reason"], item["substitute_product_id"], datetime.fromisoformat(item["occurred_at"])) for item in details["exceptions"])
        return WarehouseTask(task_id, tenant_id, warehouse_id, task_type, tuple(TaskLine(item["line_id"], item["product_id"], Decimal(item["quantity"]), item["source_bin_id"], item["destination_bin_id"], item["allow_substitution"]) for item in details["lines"]), status, assignee_id, claimed_by, tuple(details["scanned_line_ids"]), exceptions)

    def transfer(self, tenant_id: str, warehouse_id: str, transfer_id: str) -> WarehouseTransfer | None:
        row = self._connection.execute("SELECT source_warehouse_id, source_bin_id, destination_warehouse_id, destination_bin_id, status, lines, discrepancy_reason FROM warehouse_transfers WHERE tenant_id = %s AND transfer_id = %s AND (source_warehouse_id = %s OR destination_warehouse_id = %s)", (tenant_id, transfer_id, warehouse_id, warehouse_id)).fetchone()
        if row is None:
            return None
        source_warehouse_id, source_bin_id, destination_warehouse_id, destination_bin_id, status, lines, discrepancy_reason = row
        parsed = tuple(TransferLine(item["line_id"], item["product_id"], Decimal(item["quantity"]), Decimal(item["sent_quantity"]), Decimal(item["received_quantity"])) for item in lines)
        return WarehouseTransfer(transfer_id, tenant_id, source_warehouse_id, source_bin_id, destination_warehouse_id, destination_bin_id, parsed, status, discrepancy_reason)

    def _commit(self, tenant_id: str, event_type: str, aggregate_id: str, trace_id: str, write: Callable[[psycopg.Cursor[Any]], bool | None]) -> None:
        event = DurableEvent(f"{event_type}-{tenant_id}-{aggregate_id}-{trace_id}", tenant_id, event_type, "v1", trace_id, {"aggregate_id": aggregate_id}, datetime.now(UTC))
        self._outbox.commit_business_event(event, write)

    @staticmethod
    def _task_line(line: TaskLine) -> dict[str, object]:
        return {"line_id": line.line_id, "product_id": line.product_id, "quantity": str(line.quantity), "source_bin_id": line.source_bin_id, "destination_bin_id": line.destination_bin_id, "allow_substitution": line.allow_substitution}

    @staticmethod
    def _exception(item: TaskException) -> dict[str, object]:
        return {"exception_id": item.exception_id, "line_id": item.line_id, "kind": item.kind, "quantity": str(item.quantity), "reason": item.reason, "substitute_product_id": item.substitute_product_id, "occurred_at": item.occurred_at.isoformat()}
