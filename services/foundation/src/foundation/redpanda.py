"""Redpanda Kafka adapter for versioned, tenant-scoped durable events."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from kafka import KafkaConsumer, KafkaProducer

from foundation.durable_outbox import DurableEvent


class RedpandaBroker:
    """Kafka-protocol adapter used by the durable outbox publisher."""

    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._producer: Any = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            acks="all",
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
        )

    def publish(self, event: DurableEvent) -> None:
        """Publish one durable event with its ID and tenant as broker headers."""
        payload = {
            "event_id": event.event_id,
            "tenant_id": event.tenant_id,
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "trace_id": event.trace_id,
            "payload": dict(event.payload),
            "occurred_at": event.occurred_at.isoformat(),
        }
        future = self._producer.send(
            self._topic,
            key=event.event_id.encode("utf-8"),
            value=payload,
            headers=[("tenant_id", event.tenant_id.encode("utf-8"))],
        )
        future.get(timeout=10)

    def close(self) -> None:
        self._producer.flush(timeout=10)
        self._producer.close(timeout=10)


class RedpandaConsumer:
    """Reads durable events without coupling projection code to Kafka objects."""

    def __init__(self, bootstrap_servers: str, topic: str, group_id: str) -> None:
        self._consumer: Any = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            consumer_timeout_ms=5000,
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        )

    def events(self, limit: int) -> Iterator[DurableEvent]:
        """Yield up to limit events and commit offsets only after their caller receives them."""
        delivered = 0
        for message in self._consumer:
            value = message.value
            yield DurableEvent(
                event_id=str(value["event_id"]),
                tenant_id=str(value["tenant_id"]),
                event_type=str(value["event_type"]),
                schema_version=str(value["schema_version"]),
                trace_id=str(value["trace_id"]),
                payload=dict(value["payload"]),
                occurred_at=datetime.fromisoformat(str(value["occurred_at"])),
            )
            delivered += 1
            if delivered == limit:
                break
        self._consumer.commit()

    def close(self) -> None:
        self._consumer.close()
