# ruff: noqa: E501, I001
"""Durable Redpanda publisher and replay-safe order projection consumer."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from kafka import KafkaConsumer, KafkaProducer

from integrated.persistence import connect


TOPIC = "program-a.order-events.v1"
BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "redpanda:9092")


def publish_pending() -> int:
    """Publish committed events and mark them delivered only after broker acknowledgement."""
    producer = KafkaProducer(bootstrap_servers=BOOTSTRAP, value_serializer=lambda value: json.dumps(value).encode())
    delivered = 0
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT event_id, tenant_id, event_type, payload FROM demo_outbox WHERE published_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED")
        rows = cursor.fetchall()
        for event_id, tenant_id, event_type, payload in rows:
            body: dict[str, Any] = {"event_id": str(event_id), "tenant_id": str(tenant_id), "event_type": str(event_type), "payload": payload}
            producer.send(TOPIC, key=str(event_id).encode(), value=body).get(timeout=5)
            cursor.execute("UPDATE demo_outbox SET published_at = now() WHERE event_id = %s", (event_id,))
            delivered += 1
    producer.flush()
    producer.close()
    return delivered


def consume_once(timeout_ms: int = 250) -> int:
    """Consume available records, deduplicate by event id, and persist the projection."""
    consumer = KafkaConsumer(TOPIC, bootstrap_servers=BOOTSTRAP, group_id="program-a-projection-v1", auto_offset_reset="earliest", enable_auto_commit=False, value_deserializer=lambda value: json.loads(value.decode()), consumer_timeout_ms=timeout_ms)
    applied = 0
    try:
        for record in consumer:
            event = record.value
            payload = event["payload"]
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute("INSERT INTO demo_processed_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING event_id", (event["event_id"],))
                if cursor.fetchone() is not None:
                    cursor.execute("INSERT INTO demo_event_projection (tenant_id, order_id, fulfillment_status, event_id) VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, order_id) DO UPDATE SET fulfillment_status = EXCLUDED.fulfillment_status, event_id = EXCLUDED.event_id, updated_at = now()", (event["tenant_id"], payload["order_id"], payload["fulfillment_status"], event["event_id"]))
                    applied += 1
            consumer.commit()
    finally:
        consumer.close()
    return applied


def replay() -> int:
    """Rebuild the durable projection from the committed outbox without unsafe effects."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM demo_event_projection")
        cursor.execute("DELETE FROM demo_processed_events")
        cursor.execute("SELECT event_id, tenant_id, payload FROM demo_outbox ORDER BY created_at")
        rows = cursor.fetchall()
        for event_id, tenant_id, payload in rows:
            cursor.execute("INSERT INTO demo_processed_events (event_id) VALUES (%s)", (event_id,))
            cursor.execute("INSERT INTO demo_event_projection (tenant_id, order_id, fulfillment_status, event_id) VALUES (%s, %s, %s, %s)", (tenant_id, payload["order_id"], payload["fulfillment_status"], event_id))
    return len(rows)


def run_worker() -> None:
    """Recover unpublished records after restart and keep the projection current."""
    while True:
        try:
            publish_pending()
            consume_once()
        except Exception:
            time.sleep(1)
        time.sleep(0.2)


def start_worker() -> None:
    threading.Thread(target=run_worker, name="program-a-events", daemon=True).start()
