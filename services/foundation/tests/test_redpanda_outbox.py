# ruff: noqa: I001
"""Redpanda end-to-end evidence for durable outbox delivery and recovery."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from foundation.durable_outbox import DurableEvent
from foundation.redpanda import RedpandaBroker, RedpandaConsumer

BOOTSTRAP = os.environ.get("TEST_REDPANDA_BOOTSTRAP_SERVERS")


def event(event_id: str, tenant_id: str = "tenant-a") -> DurableEvent:
    return DurableEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type="OrganizationChanged",
        schema_version="v1",
        trace_id="trace-redpanda",
        payload={"organization_id": event_id},
        occurred_at=datetime.now(UTC),
    )


@pytest.mark.redpanda
def test_redpanda_publish_consume_and_duplicate_delivery() -> None:
    if not BOOTSTRAP:
        pytest.skip("TEST_REDPANDA_BOOTSTRAP_SERVERS is required")
    topic = f"outbox-e2e-{uuid.uuid4().hex}"
    broker = RedpandaBroker(BOOTSTRAP, topic)
    try:
        item = event("event-duplicate")
        broker.publish(item)
        broker.publish(item)
    finally:
        broker.close()

    consumer = RedpandaConsumer(BOOTSTRAP, topic, f"consumer-{uuid.uuid4().hex}")
    try:
        received = list(consumer.events(2))
    finally:
        consumer.close()
    assert [item.event_id for item in received] == ["event-duplicate", "event-duplicate"]
    assert {item.tenant_id for item in received} == {"tenant-a"}


@pytest.mark.redpanda
def test_redpanda_restart_recovery_and_tenant_metadata() -> None:
    if not BOOTSTRAP:
        pytest.skip("TEST_REDPANDA_BOOTSTRAP_SERVERS is required")
    topic = f"outbox-restart-{uuid.uuid4().hex}"
    broker = RedpandaBroker(BOOTSTRAP, topic)
    try:
        broker.publish(event("event-recovery", "tenant-b"))
    finally:
        broker.close()

    restarted_consumer = RedpandaConsumer(BOOTSTRAP, topic, f"consumer-{uuid.uuid4().hex}")
    try:
        received = list(restarted_consumer.events(1))
    finally:
        restarted_consumer.close()
    assert [(item.event_id, item.tenant_id) for item in received] == [
        ("event-recovery", "tenant-b")
    ]
