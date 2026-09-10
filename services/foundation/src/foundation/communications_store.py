"""PostgreSQL persistence for governed communication messages and outbox events."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol


class DatabaseCursor(Protocol):
    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


class DatabaseConnection(Protocol):
    def cursor(self) -> DatabaseCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass(frozen=True)
class StoredCommunication:
    message_id: str
    tenant_id: str
    channel: str
    recipient: str
    template_id: str
    idempotency_key: str
    status: str
    attempts: int
    provider_message_id: str | None
    failure_kind: str | None


class PostgresCommunicationStore:
    """Writes state transitions and redacted outbox events in one database transaction."""

    def __init__(self, connection: DatabaseConnection) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[DatabaseCursor]:
        cursor = self._connection.cursor()
        try:
            yield cursor
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def write_message_transition_and_outbox(
        self,
        cursor: DatabaseCursor,
        message: StoredCommunication,
        prior_status: str | None,
        event_id: str,
        trace_id: str,
    ) -> None:
        cursor.execute(
            "INSERT INTO communication_messages "
            "(message_id, tenant_id, channel, recipient, template_id, idempotency_key, status, "
            "attempts, provider_message_id, failure_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (message_id) DO UPDATE SET status = EXCLUDED.status, "
            "attempts = EXCLUDED.attempts, provider_message_id = EXCLUDED.provider_message_id, "
            "failure_kind = EXCLUDED.failure_kind, updated_at = CURRENT_TIMESTAMP",
            (
                message.message_id,
                message.tenant_id,
                message.channel,
                message.recipient,
                message.template_id,
                message.idempotency_key,
                message.status,
                message.attempts,
                message.provider_message_id,
                message.failure_kind,
            ),
        )
        cursor.execute(
            "INSERT INTO communication_transitions "
            "(event_id, message_id, tenant_id, from_status, to_status, trace_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (event_id, message.message_id, message.tenant_id, prior_status, message.status, trace_id),
        )
        payload = json.dumps({"message_id": message.message_id, "status": message.status})
        cursor.execute(
            "INSERT INTO communication_outbox "
            "(event_id, message_id, tenant_id, event_type, trace_id, payload) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (
                f"outbox-{event_id}",
                message.message_id,
                message.tenant_id,
                "communications.message.changed.v1",
                trace_id,
                payload,
            ),
        )

    def recover_active_for_tenant(
        self,
        cursor: DatabaseCursor,
        tenant_id: str,
    ) -> list[StoredCommunication]:
        cursor.execute(
            "SELECT message_id, tenant_id, channel, recipient, template_id, idempotency_key, status, "
            "attempts, provider_message_id, failure_kind FROM communication_messages "
            "WHERE tenant_id = %s AND status IN ('queued', 'retryable', 'accepted')",
            (tenant_id,),
        )
        return [StoredCommunication(*row) for row in cursor.fetchall()]

    def record_webhook_once(
        self,
        cursor: DatabaseCursor,
        tenant_id: str,
        provider: str,
        event_id: str,
        sequence: int,
    ) -> None:
        cursor.execute(
            "INSERT INTO communication_webhook_receipts "
            "(tenant_id, provider, event_id, sequence) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, provider, event_id) DO NOTHING",
            (tenant_id, provider, event_id, sequence),
        )
