# ruff: noqa: E501
"""PostgreSQL-backed outbox delivery and replay-safe projection processing."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class DurableEvent:
    """A versioned, tenant-scoped event ready for durable delivery."""

    event_id: str
    tenant_id: str
    event_type: str
    schema_version: str
    trace_id: str
    payload: Mapping[str, object]
    occurred_at: datetime


class EventBroker(Protocol):
    """Broker adapter boundary. Redpanda/Kafka adapters implement this contract."""

    def publish(self, event: DurableEvent) -> None:
        """Deliver an event. An exception leaves the record pending for recovery."""


BusinessWrite = Callable[[psycopg.Cursor[Any]], None]
ExternalEffect = Callable[[DurableEvent], None]


class DurableOutboxStore:
    """Coordinates committed facts, at-least-once delivery, and safe replay."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def commit_business_event(self, event: DurableEvent, business_write: BusinessWrite) -> None:
        """Commit a business mutation and its event in one PostgreSQL transaction."""
        self._validate(event)
        with self._connection.transaction(), self._connection.cursor() as cursor:
            business_write(cursor)
            cursor.execute(
                "INSERT INTO durable_outbox_records "
                "(event_id, tenant_id, event_type, schema_version, trace_id, payload, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
                (event.event_id, event.tenant_id, event.event_type, event.schema_version, event.trace_id, json.dumps(dict(event.payload)), event.occurred_at),
            )
            self._audit(cursor, event, "outbox.commit", "committed")

    def pending(self, limit: int = 100) -> tuple[DurableEvent, ...]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT event_id, tenant_id, event_type, schema_version, trace_id, payload, occurred_at "
                "FROM durable_outbox_records WHERE published_at IS NULL "
                "AND event_id NOT IN (SELECT event_id FROM outbox_dead_letters) "
                "ORDER BY occurred_at, event_id LIMIT %s",
                (limit,),
            )
            return tuple(self._event(row) for row in cursor.fetchall())

    def publish_pending(self, broker: EventBroker, max_attempts: int = 3) -> tuple[str, ...]:
        """Publish committed records. A post-publish crash intentionally permits a retry."""
        published: list[str] = []
        for event in self.pending():
            try:
                broker.publish(event)
            except Exception as error:
                self._record_publish_failure(event, str(error), max_attempts)
                continue
            with self._connection.transaction(), self._connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE durable_outbox_records SET published_at = now(), publish_attempts = "
                    "publish_attempts + 1, last_error = NULL WHERE event_id = %s AND published_at IS NULL",
                    (event.event_id,),
                )
                self._audit(cursor, event, "outbox.publish", "published")
                published.append(event.event_id)
        return tuple(published)

    def consume(self, consumer_name: str, event: DurableEvent, projection_key: str, external_effect: ExternalEffect, *, replay: bool = False) -> bool:
        """Apply one logical projection effect. Replay never invokes external effects."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("SELECT tenant_id FROM durable_outbox_records WHERE event_id = %s", (event.event_id,))
            row = cursor.fetchone()
            if row is None or row[0] != event.tenant_id:
                raise PermissionError("event is outside tenant scope")
            cursor.execute(
                "INSERT INTO consumer_event_progress (consumer_name, tenant_id, event_id, replayed) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING event_id",
                (consumer_name, event.tenant_id, event.event_id, replay),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "INSERT INTO replay_projection_effects (consumer_name, tenant_id, projection_key, logical_effect_count) "
                "VALUES (%s, %s, %s, 1) ON CONFLICT (consumer_name, tenant_id, projection_key) "
                "DO UPDATE SET logical_effect_count = replay_projection_effects.logical_effect_count + 1, updated_at = now()",
                (consumer_name, event.tenant_id, projection_key),
            )
            self._audit(cursor, event, "projection.replay" if replay else "projection.consume", "applied")
        if not replay:
            external_effect(event)
        return True

    def replay(self, consumer_name: str, tenant_id: str, events: Sequence[DurableEvent], projection_key: str) -> tuple[bool, ...]:
        """Rebuild a tenant projection from committed facts without unsafe effects."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("DELETE FROM consumer_event_progress WHERE consumer_name = %s AND tenant_id = %s", (consumer_name, tenant_id))
            cursor.execute("DELETE FROM replay_projection_effects WHERE consumer_name = %s AND tenant_id = %s", (consumer_name, tenant_id))
        return tuple(self.consume(consumer_name, event, projection_key, lambda _: None, replay=True) for event in events if event.tenant_id == tenant_id)

    def projection_count(self, consumer_name: str, tenant_id: str, projection_key: str) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT logical_effect_count FROM replay_projection_effects WHERE consumer_name = %s AND tenant_id = %s AND projection_key = %s", (consumer_name, tenant_id, projection_key))
            row = cursor.fetchone()
            return 0 if row is None else int(row[0])

    def _record_publish_failure(self, event: DurableEvent, error: str, max_attempts: int) -> None:
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute("UPDATE durable_outbox_records SET publish_attempts = publish_attempts + 1, last_error = %s WHERE event_id = %s RETURNING publish_attempts", (error, event.event_id))
            returned_attempts = cursor.fetchone()
            if returned_attempts is None:
                raise LookupError(f"outbox event {event.event_id} does not exist")
            attempts = int(returned_attempts[0])
            result = "retrying"
            if attempts >= max_attempts:
                cursor.execute("INSERT INTO outbox_dead_letters (dead_letter_id, event_id, tenant_id, reason) VALUES (%s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING", (f"dead-{event.event_id}", event.event_id, event.tenant_id, error))
                result = "dead_lettered"
            self._audit(cursor, event, "outbox.publish", result)

    @staticmethod
    def _validate(event: DurableEvent) -> None:
        if not all((event.event_id, event.tenant_id, event.event_type, event.schema_version, event.trace_id)):
            raise ValueError("events require identifiers, tenant metadata, schema version, and trace context")
        if event.occurred_at.tzinfo is None:
            raise ValueError("event timestamps must be timezone-aware")

    @staticmethod
    def _event(row: Mapping[str, object]) -> DurableEvent:
        payload = cast(Mapping[str, object], row["payload"])
        occurred_at = cast(datetime, row["occurred_at"])
        return DurableEvent(str(row["event_id"]), str(row["tenant_id"]), str(row["event_type"]), str(row["schema_version"]), str(row["trace_id"]), dict(payload), occurred_at)

    @staticmethod
    def _audit(cursor: psycopg.Cursor[Any], event: DurableEvent, source: str, result: str) -> None:
        cursor.execute(
            "INSERT INTO audit_records (audit_id, tenant_id, actor_id, authority, source, reason, policy, trace_id, result) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (f"audit-{source}-{event.event_id}-{result}", event.tenant_id, "event-platform", "system", source, event.event_type, event.schema_version, event.trace_id, result),
        )
