"""PostgreSQL persistence for tenant-scoped Product Information."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.product_information import ProductRecord, ProductValidationError


class DurableProductStore:
    """Writes product state and ProductChanged events in one transaction."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def create(self, record: ProductRecord, trace_id: str) -> None:
        event = self._event(record, "ProductChanged", trace_id)
        self._outbox.commit_business_event(event, lambda cursor: self._insert(cursor, record))

    def change_lifecycle(self, record: ProductRecord, trace_id: str) -> None:
        event = self._event(record, "ProductLifecycleChanged", trace_id)

        def write(cursor: psycopg.Cursor[Any]) -> None:
            cursor.execute(
                "UPDATE products SET lifecycle_status = %s, updated_at = now() "
                "WHERE tenant_id = %s AND product_id = %s",
                (record.lifecycle_status, record.tenant_id, record.product_id),
            )
            if cursor.rowcount != 1:
                raise ProductValidationError("product is outside the tenant scope")

        self._outbox.commit_business_event(event, write)

    def get(self, tenant_id: str, product_id: str) -> ProductRecord:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT product_id, tenant_id, name, unit_of_measure, lifecycle_status "
                "FROM products WHERE tenant_id = %s AND product_id = %s",
                (tenant_id, product_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ProductValidationError("product is outside the tenant scope")
            cursor.execute(
                "SELECT identifier FROM product_identifiers WHERE tenant_id = %s "
                "AND product_id = %s ORDER BY identifier",
                (tenant_id, product_id),
            )
            identifiers = tuple(str(item["identifier"]) for item in cursor.fetchall())
            cursor.execute(
                "SELECT variant_id FROM product_variants WHERE tenant_id = %s "
                "AND product_id = %s ORDER BY variant_id",
                (tenant_id, product_id),
            )
            variants = tuple(str(item["variant_id"]) for item in cursor.fetchall())
        return ProductRecord(
            str(row["product_id"]),
            str(row["tenant_id"]),
            str(row["name"]),
            str(row["unit_of_measure"]),
            str(row["lifecycle_status"]),
            identifiers,
            variants,
        )

    @staticmethod
    def _event(record: ProductRecord, event_type: str, trace_id: str) -> DurableEvent:
        return DurableEvent(
            f"{event_type}-{record.tenant_id}-{record.product_id}-{trace_id}",
            record.tenant_id,
            event_type,
            "v1",
            trace_id,
            {"product_id": record.product_id, "lifecycle_status": record.lifecycle_status},
            datetime.now(UTC),
        )

    @staticmethod
    def _insert(cursor: psycopg.Cursor[Any], record: ProductRecord) -> None:
        cursor.execute(
            "INSERT INTO products (product_id, tenant_id, name, unit_of_measure, lifecycle_status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                record.product_id,
                record.tenant_id,
                record.name,
                record.unit_of_measure,
                record.lifecycle_status,
            ),
        )
        for identifier in record.identifiers:
            cursor.execute(
                "INSERT INTO product_identifiers (tenant_id, identifier, product_id) "
                "VALUES (%s, %s, %s)",
                (record.tenant_id, identifier, record.product_id),
            )
        for variant_id in record.variant_ids:
            cursor.execute(
                "INSERT INTO product_variants (tenant_id, variant_id, product_id) VALUES (%s, %s, %s)",
                (record.tenant_id, variant_id, record.product_id),
            )
