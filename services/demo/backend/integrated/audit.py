"""Durable audit adapter for governed Program A operations."""

from __future__ import annotations

import json
import uuid

from integrated.persistence import connect


def record(tenant_id: str, actor_id: str, action: str, subject_id: str, trace_id: str) -> None:
    """Append a tenant-scoped audit record in the same durable database."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO demo_audit
            (audit_id, tenant_id, actor_id, action, subject_id, trace_id, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
            (str(uuid.uuid4()), tenant_id, actor_id, action, subject_id, trace_id,
             json.dumps({"action": action})),
        )
