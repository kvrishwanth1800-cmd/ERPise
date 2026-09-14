"""Durable tenant-scoped support case lifecycle adapter."""
from __future__ import annotations

import uuid
from typing import Any

from integrated.persistence import connect


def create(tenant_id: str, opened_by: str, subject: str, details: str) -> dict[str, Any]:
    case_id = f"case-{uuid.uuid4()}"
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO demo_cases (case_id, tenant_id, subject, details, status, opened_by) VALUES (%s, %s, %s, %s, 'open', %s)", (case_id, tenant_id, subject, details, opened_by))
    return {"case_id": case_id, "status": "open"}


def transition(tenant_id: str, case_id: str, status: str, assignee: str | None = None) -> dict[str, Any]:
    allowed = {"assigned", "escalated", "resolved"}
    if status not in allowed:
        raise ValueError("invalid_case_status")
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE demo_cases SET status = %s, assignee = COALESCE(%s, assignee), updated_at = now() WHERE tenant_id = %s AND case_id = %s RETURNING case_id, status, assignee", (status, assignee, tenant_id, case_id))
        row = cursor.fetchone()
    if row is None:
        raise ValueError("case_not_found")
    return {"case_id": str(row[0]), "status": str(row[1]), "assignee": row[2]}


def list_cases(tenant_id: str) -> list[dict[str, Any]]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT case_id, subject, details, status, opened_by, assignee FROM demo_cases WHERE tenant_id = %s ORDER BY updated_at DESC", (tenant_id,))
        return [{"case_id": str(row[0]), "subject": str(row[1]), "details": str(row[2]), "status": str(row[3]), "opened_by": str(row[4]), "assignee": row[5]} for row in cursor.fetchall()]
