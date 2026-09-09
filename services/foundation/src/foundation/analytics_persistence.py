"""PostgreSQL persistence for replayable analytics projection facts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from foundation.analytics import OperationalMetricEvent, ReportFilter
from foundation.durable_outbox import DurableEvent, DurableOutboxStore


class DurableAnalyticsStore:
    """Stores governed projection rows with source-event idempotency."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def apply(self, event: OperationalMetricEvent, trace_id: str) -> bool:
        durable_event = DurableEvent(
            event_id=f"MetricProjectionUpdated-{event.event_id}",
            tenant_id=event.tenant_id,
            event_type="MetricProjectionUpdated",
            schema_version="v1",
            trace_id=trace_id,
            payload={"source_event_id": event.event_id, "metric": event.metric},
            occurred_at=datetime.now(UTC),
        )
        applied = False

        def write(cursor: psycopg.Cursor[Any]) -> bool:
            nonlocal applied
            cursor.execute(
                "INSERT INTO analytics_projection_events "
                "(tenant_id, source_event_id, metric, amount, currency, occurred_at, store_id, warehouse_id, channel, entity_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, source_event_id) DO NOTHING",
                (
                    event.tenant_id, event.event_id, event.metric, event.amount,
                    event.currency, event.occurred_at, event.store_id, event.warehouse_id,
                    event.channel, event.entity_id,
                ),
            )
            applied = cursor.rowcount > 0
            return applied

        self._outbox.commit_business_event(durable_event, write)
        return applied

    def rebuild(
        self, tenant_id: str, events: Iterable[OperationalMetricEvent], trace_id: str
    ) -> int:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("DELETE FROM analytics_projection_events WHERE tenant_id = %s", (tenant_id,))
            cursor.execute(
                "INSERT INTO analytics_projection_rebuilds (tenant_id, trace_id, rebuilt_at) "
                "VALUES (%s, %s, now())",
                (tenant_id, trace_id),
            )
        return sum(
            self.apply(event, trace_id)
            for event in sorted(events, key=lambda item: (item.occurred_at, item.event_id))
            if event.tenant_id == tenant_id
        )

    def query(self, tenant_id: str, filters: ReportFilter) -> tuple[dict[str, object], ...]:
        if filters.ends_on < filters.starts_on:
            raise ValueError("report end date must not precede start date")
        clauses = [
            "tenant_id = %s", "occurred_at >= %s", "occurred_at < %s",
        ]
        values: list[object] = [
            tenant_id,
            datetime.combine(filters.starts_on, datetime.min.time(), UTC),
            datetime.combine(filters.ends_on, datetime.max.time(), UTC),
        ]
        for column, value in (
            ("store_id", filters.store_id), ("warehouse_id", filters.warehouse_id),
            ("channel", filters.channel), ("entity_id", filters.entity_id),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                values.append(value)
        statement = (
            "SELECT metric, currency, sum(amount) AS value, count(*) AS event_count, "
            "max(occurred_at) AS freshness FROM analytics_projection_events WHERE "
            + " AND ".join(clauses)
            + " GROUP BY metric, currency ORDER BY metric, currency"
        )
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(statement, values)
            return tuple(dict(row) for row in cursor.fetchall())

    def record_export(
        self, tenant_id: str, actor_id: str, filters: ReportFilter, trace_id: str
    ) -> None:
        event = DurableEvent(
            event_id=f"ExportCompleted-{tenant_id}-{trace_id}", tenant_id=tenant_id,
            event_type="ExportCompleted", schema_version="v1", trace_id=trace_id,
            payload={"filters": json.dumps(filters, default=str)}, occurred_at=datetime.now(UTC),
        )
        self._outbox.commit_business_event(
            event,
            lambda cursor: self._insert_export(cursor, tenant_id, actor_id, filters, trace_id),
        )

    @staticmethod
    def _insert_export(
        cursor: psycopg.Cursor[Any], tenant_id: str, actor_id: str,
        filters: ReportFilter, trace_id: str,
    ) -> bool:
        cursor.execute(
            "INSERT INTO analytics_report_exports (tenant_id, trace_id, actor_id, filters) "
            "VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (tenant_id, trace_id) DO NOTHING",
            (tenant_id, trace_id, actor_id, json.dumps(filters, default=str)),
        )
        return cursor.rowcount > 0
