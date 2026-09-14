# ruff: noqa: E501
"""Durable session adapter for the modular demonstration runtime."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from integrated.config import IDENTITY
from integrated.persistence import connect


def create() -> dict[str, str]:
    """Persist and return a new tenant-scoped demonstration session."""
    session_id = secrets.token_urlsafe(32)
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO demo_sessions (session_id, tenant_id, user_id, role, expires_at) VALUES (%s, %s, %s, %s, %s)", (session_id, IDENTITY.tenant_id, IDENTITY.user_id, IDENTITY.role, datetime.now(UTC) + timedelta(hours=8)))
    return {"session_id": session_id, "tenant_id": IDENTITY.tenant_id, "user_id": IDENTITY.user_id, "role": IDENTITY.role}


def get(session_id: str | None) -> dict[str, str] | None:
    """Load an active session only when it remains in its tenant scope."""
    if not session_id:
        return None
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT tenant_id, user_id, role FROM demo_sessions WHERE session_id = %s AND revoked_at IS NULL AND expires_at > now()", (session_id,))
        row = cursor.fetchone()
    if row is None:
        return None
    return {"session_id": session_id, "tenant_id": str(row[0]), "user_id": str(row[1]), "role": str(row[2])}


def revoke(session_id: str | None) -> None:
    """Revoke the supplied durable session if present."""
    if session_id:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE demo_sessions SET revoked_at = now() WHERE session_id = %s", (session_id,))
