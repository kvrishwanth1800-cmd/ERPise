"""PostgreSQL persistence for Phase 1 tenant and evidence state.

Each public write method requires one caller-owned transaction. This keeps a
hierarchy mutation and its OrganizationChanged outbox record atomic.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


class DurableFoundationStore:
    """Tenant-scoped PostgreSQL repository. It does not publish outbox events."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def create_organization(
        self,
        tenant_id: str,
        organization_id: str,
        parent_organization_id: str | None,
        settings: Mapping[str, str],
        trace_id: str,
    ) -> None:
        payload = {
            "organization_id": organization_id,
            "parent_organization_id": parent_organization_id,
            "settings": dict(settings),
        }
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO organizations "
                "(organization_id, tenant_id, parent_organization_id, settings) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (
                    organization_id,
                    tenant_id,
                    parent_organization_id,
                    json.dumps(dict(settings)),
                ),
            )
            self._append_hierarchy_event(
                cursor, tenant_id, organization_id, "created", trace_id, payload
            )

    def update_organization_settings(
        self,
        tenant_id: str,
        organization_id: str,
        settings: Mapping[str, str],
        trace_id: str,
    ) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE organizations SET settings = %s::jsonb, version = version + 1, "
                "updated_at = now() WHERE organization_id = %s AND tenant_id = %s",
                (json.dumps(dict(settings)), organization_id, tenant_id),
            )
            if cursor.rowcount != 1:
                raise PermissionError("organization is outside tenant scope")
            self._append_hierarchy_event(
                cursor,
                tenant_id,
                organization_id,
                "updated",
                trace_id,
                {"settings": dict(settings)},
            )

    def delete_organization(self, tenant_id: str, organization_id: str, trace_id: str) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM organizations WHERE organization_id = %s AND tenant_id = %s",
                (organization_id, tenant_id),
            )
            if cursor.rowcount != 1:
                raise PermissionError("organization is outside tenant scope or protected")
            self._append_hierarchy_event(
                cursor, tenant_id, organization_id, "deleted", trace_id, {}
            )

    def effective_settings(self, tenant_id: str, organization_id: str) -> dict[str, str]:
        query = """WITH RECURSIVE lineage AS (
          SELECT organization_id, parent_organization_id, settings, 0 AS depth
          FROM organizations WHERE organization_id=%s AND tenant_id=%s
          UNION ALL
          SELECT parent.organization_id, parent.parent_organization_id,
                 parent.settings, lineage.depth + 1
          FROM organizations parent JOIN lineage
            ON lineage.parent_organization_id=parent.organization_id
          WHERE parent.tenant_id=%s)
          SELECT settings FROM lineage ORDER BY depth DESC"""
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (organization_id, tenant_id, tenant_id))
            rows = cursor.fetchall()
        if not rows:
            raise PermissionError("organization is outside tenant scope")
        resolved: dict[str, str] = {}
        for row in rows:
            resolved.update(row["settings"])
        return resolved

    def grant(
        self,
        grant_id: str,
        principal_id: str,
        tenant_id: str,
        action: str,
        organization_ids: set[str],
        record_ids: set[str],
    ) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO authorization_grants "
                "(grant_id, principal_id, tenant_id, action, organization_ids, record_ids) "
                "VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                (
                    grant_id,
                    principal_id,
                    tenant_id,
                    action,
                    json.dumps(sorted(organization_ids)),
                    json.dumps(sorted(record_ids)),
                ),
            )

    def record_audit(
        self,
        tenant_id: str,
        actor_id: str,
        authority: str,
        source: str,
        reason: str,
        policy: str,
        trace_id: str,
        result: str,
    ) -> str:
        audit_id = str(uuid4())
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO audit_records "
                "(audit_id,tenant_id,actor_id,authority,source,reason,policy,trace_id,result) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    audit_id,
                    tenant_id,
                    actor_id,
                    authority,
                    source,
                    reason,
                    policy,
                    trace_id,
                    result,
                ),
            )
        return audit_id

    @staticmethod
    def _append_hierarchy_event(
        cursor: psycopg.Cursor[Any],
        tenant_id: str,
        organization_id: str,
        action: str,
        trace_id: str,
        payload: Mapping[str, object],
    ) -> None:
        cursor.execute(
            "INSERT INTO hierarchy_outbox_records "
            "(event_id,tenant_id,organization_id,action,trace_id,payload,occurred_at) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)",
            (
                str(uuid4()),
                tenant_id,
                organization_id,
                action,
                trace_id,
                json.dumps(dict(payload)),
                datetime.now(UTC),
            ),
        )
