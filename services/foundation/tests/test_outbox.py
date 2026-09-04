from datetime import UTC, datetime

import pytest
from foundation.outbox import (
    DomainEvent,
    OutboxPublisher,
    OutboxValidationError,
    ReplaySafeConsumer,
    TransactionalOutbox,
)


def event(event_id: str = "event-a") -> DomainEvent:
    return DomainEvent(
        version="v1",
        event_id=event_id,
        event_type="order.committed",
        occurred_at=datetime.now(UTC),
        trace_id="trace-a",
        payload={"order_id": "order-a"},
    )


def apply_projection(projection: dict[str, object], committed_event: DomainEvent) -> None:
    projection[committed_event.event_id] = committed_event.payload


def test_commit_stages_state_and_outbox_record_together() -> None:
    outbox = TransactionalOutbox()

    result = outbox.commit(lambda state: state.update({"order-a": "committed"}), event())

    assert result is None
    assert outbox.state == {"order-a": "committed"}
    assert outbox.records[0].event.event_id == "event-a"
    assert outbox.pending_records[0].record_id == "outbox-event-a"


def test_failed_state_operation_does_not_commit_an_outbox_record() -> None:
    outbox = TransactionalOutbox()

    with pytest.raises(RuntimeError, match="operation failed"):
        outbox.commit(lambda _state: (_ for _ in ()).throw(RuntimeError("operation failed")), event())

    assert outbox.state == {}
    assert outbox.records == ()


def test_publisher_retries_only_undelivered_committed_records() -> None:
    outbox = TransactionalOutbox()
    outbox.commit(lambda state: state.update({"order-a": "committed"}), event())
    published: list[str] = []
    publisher = OutboxPublisher()

    delivered = publisher.publish_pending(
        outbox, lambda committed_event: published.append(committed_event.event_id)
    )
    repeated = publisher.publish_pending(
        outbox, lambda committed_event: published.append(committed_event.event_id)
    )

    assert [record.record_id for record in delivered] == ["outbox-event-a"]
    assert repeated == ()
    assert published == ["event-a"]


def test_duplicate_delivery_has_one_logical_effect() -> None:
    consumer = ReplaySafeConsumer()
    unsafe_calls: list[str] = []

    first = consumer.consume(
        event(),
        apply_projection,
        lambda committed_event: unsafe_calls.append(committed_event.event_id),
    )
    duplicate = consumer.consume(
        event(),
        apply_projection,
        lambda committed_event: unsafe_calls.append(committed_event.event_id),
    )

    assert first.applied is True
    assert duplicate.applied is False
    assert consumer.projection == {"event-a": {"order_id": "order-a"}}
    assert unsafe_calls == ["event-a"]


def test_replay_rebuilds_projection_without_unsafe_effects() -> None:
    consumer = ReplaySafeConsumer()
    unsafe_calls: list[str] = []
    events = (event("event-a"), event("event-b"), event("event-a"))

    results = consumer.replay(events, apply_projection)

    assert [result.applied for result in results] == [True, True, False]
    assert consumer.projection == {
        "event-a": {"order_id": "order-a"},
        "event-b": {"order_id": "order-a"},
    }
    assert unsafe_calls == []


def test_event_without_trace_context_is_rejected_before_commit() -> None:
    outbox = TransactionalOutbox()
    invalid_event = DomainEvent(
        version="v1",
        event_id="event-a",
        event_type="order.committed",
        occurred_at=datetime.now(UTC),
        trace_id="",
        payload={},
    )

    with pytest.raises(OutboxValidationError, match="trace context"):
        outbox.commit(lambda state: state.update({"order-a": "committed"}), invalid_event)

    assert outbox.state == {}
    assert outbox.records == ()
