"""PostgreSQL persistence for tenant-scoped assortment eligibility."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from foundation.assortment import (
    AssortmentEligibility,
    AssortmentRecord,
    AssortmentScope,
)
from foundation.durable_outbox import DurableEvent, DurableOutboxStore


class DurableAssortmentStore:
    """Writes assortment state and publication events in one transaction."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def publish(self, record: AssortmentRecord, trace_id: str) -> None:
        event = DurableEvent(
            f"AssortmentPublished-{record.tenant_id}-{record.assortment_id}-{trace_id}",
            record.tenant_id,
            "AssortmentPublished",
            "v1",
            trace_id,
            {"assortment_id": record.assortment_id},
            datetime.now(UTC),
        )
        self._outbox.commit_business_event(
            event, lambda cursor: self._insert(cursor, record)
        )

    def eligibility(
        self, tenant_id: str, scope: AssortmentScope, at: datetime
    ) -> AssortmentEligibility:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT assortment_id FROM assortments "
                "WHERE tenant_id = %s AND effective_from <= %s "
                "AND (effective_until IS NULL OR effective_until > %s) "
                "AND (store_id IS NULL OR store_id = %s) "
                "AND (channel_id IS NULL OR channel_id = %s) "
                "AND (segment_id IS NULL OR segment_id = %s) "
                "ORDER BY "
                "((store_id IS NOT NULL)::int + (channel_id IS NOT NULL)::int + "
                "(segment_id IS NOT NULL)::int) DESC, effective_from DESC, assortment_id ASC "
                "LIMIT 1",
                (tenant_id, at, at, scope.store_id, scope.channel_id, scope.segment_id),
            )
            selected = cursor.fetchone()
            if selected is None:
                return AssortmentEligibility(None, ())
            assortment_id = str(selected["assortment_id"])
            cursor.execute(
                "SELECT product_id FROM assortment_products "
                "WHERE tenant_id = %s AND assortment_id = %s ORDER BY product_id",
                (tenant_id, assortment_id),
            )
            return AssortmentEligibility(
                assortment_id,
                tuple(str(row["product_id"]) for row in cursor.fetchall()),
            )

    @staticmethod
    def _insert(cursor: psycopg.Cursor[Any], record: AssortmentRecord) -> None:
        cursor.execute(
            "INSERT INTO assortments (tenant_id, assortment_id, store_id, channel_id, "
            "segment_id, effective_from, effective_until) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                record.tenant_id,
                record.assortment_id,
                record.scope.store_id,
                record.scope.channel_id,
                record.scope.segment_id,
                record.effective_from,
                record.effective_until,
            ),
        )
        for product_id in record.product_ids:
            cursor.execute(
                "INSERT INTO assortment_products (tenant_id, assortment_id, product_id) "
                "VALUES (%s, %s, %s)",
                (record.tenant_id, record.assortment_id, product_id),
            )
