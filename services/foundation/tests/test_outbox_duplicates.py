from datetime import UTC, datetime

import pytest
from foundation.outbox import DomainEvent, OutboxValidationError, TransactionalOutbox


def test_duplicate_event_identifier_does_not_commit_state_or_second_record() -> None:
    outbox = TransactionalOutbox()
    event = DomainEvent(
        version="v1",
        event_id="event-a",
        event_type="order.committed",
        occurred_at=datetime.now(UTC),
        trace_id="trace-a",
        payload={"order_id": "order-a"},
    )

    outbox.commit(lambda state: state.update({"order-a": "committed"}), event)

    with pytest.raises(OutboxValidationError, match="identifiers must be unique"):
        outbox.commit(lambda state: state.update({"order-b": "committed"}), event)

    assert outbox.state == {"order-a": "committed"}
    assert [record.event.event_id for record in outbox.records] == ["event-a"]
