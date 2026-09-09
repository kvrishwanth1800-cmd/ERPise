"""In-memory transactional outbox and replay-safe projection primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar


class OutboxValidationError(ValueError):
    """Raised when a committed event or delivery operation is invalid."""


T = TypeVar("T")


@dataclass(frozen=True)
class DomainEvent:
    """Versioned event envelope preserved from a committed operational change."""

    version: str
    event_id: str
    event_type: str
    occurred_at: datetime
    trace_id: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class OutboxRecord:
    """A committed event awaiting or recording broker delivery."""

    record_id: str
    event: DomainEvent
    committed_at: datetime
    delivered_at: datetime | None = None


@dataclass(frozen=True)
class ProjectionResult:
    """The durable logical projection effect of processing one event."""

    event_id: str
    replay: bool
    applied: bool


class TransactionalOutbox:
    """Commits managed state and an outbox event as one in-memory transition."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}
        self._records: list[OutboxRecord] = []

    @property
    def records(self) -> tuple[OutboxRecord, ...]:
        return tuple(self._records)

    @property
    def state(self) -> Mapping[str, object]:
        return dict(self._state)

    def commit(self, operation: Callable[[dict[str, object]], T], event: DomainEvent) -> T:
        """Stage a state operation and add its event only after it succeeds."""
        self._validate_event(event)
        staged_state = dict(self._state)
        result = operation(staged_state)
        record = OutboxRecord(
            record_id=f"outbox-{event.event_id}",
            event=event,
            committed_at=datetime.now(UTC),
        )
        self._state = staged_state
        self._records.append(record)
        return result

    def pending_records(self) -> tuple[OutboxRecord, ...]:
        return tuple(record for record in self._records if record.delivered_at is None)

    def mark_delivered(self, record_id: str) -> OutboxRecord:
        """Record successful delivery without changing the committed event or state."""
        for index, record in enumerate(self._records):
            if record.record_id == record_id:
                if record.delivered_at is not None:
                    return record
                delivered = OutboxRecord(
                    record_id=record.record_id,
                    event=record.event,
                    committed_at=record.committed_at,
                    delivered_at=datetime.now(UTC),
                )
                self._records[index] = delivered
                return delivered
        raise OutboxValidationError("Outbox record does not exist.")

    @staticmethod
    def _validate_event(event: DomainEvent) -> None:
        required_values = (event.version, event.event_id, event.event_type, event.trace_id)
        if any(not value for value in required_values):
            raise OutboxValidationError(
                "Committed events require version, identifiers, and trace context."
            )
        if event.occurred_at.tzinfo is None:
            raise OutboxValidationError("Committed event times must be timezone-aware.")


class OutboxPublisher:
    """Publishes only committed records and tolerates retry after delivery failure."""

    def publish_pending(
        self, outbox: TransactionalOutbox, publish: Callable[[DomainEvent], None]
    ) -> tuple[OutboxRecord, ...]:
        delivered: list[OutboxRecord] = []
        for record in outbox.pending_records():
            publish(record.event)
            delivered.append(outbox.mark_delivered(record.record_id))
        return tuple(delivered)


class ReplaySafeConsumer:
    """Applies one logical effect per event and excludes unsafe effects during replay."""

    def __init__(self) -> None:
        self._processed_event_ids: set[str] = set()
        self.projection: dict[str, Mapping[str, object]] = {}

    def consume(
        self,
        event: DomainEvent,
        apply_projection: Callable[[dict[str, Mapping[str, object]], DomainEvent], None],
        unsafe_effect: Callable[[DomainEvent], None],
    ) -> ProjectionResult:
        if event.event_id in self._processed_event_ids:
            return ProjectionResult(event_id=event.event_id, replay=False, applied=False)
        apply_projection(self.projection, event)
        unsafe_effect(event)
        self._processed_event_ids.add(event.event_id)
        return ProjectionResult(event_id=event.event_id, replay=False, applied=True)

    def replay(
        self,
        events: tuple[DomainEvent, ...],
        apply_projection: Callable[[dict[str, Mapping[str, object]], DomainEvent], None],
    ) -> tuple[ProjectionResult, ...]:
        """Rebuild a projection without executing external or unsafe effects."""
        self.projection = {}
        self._processed_event_ids = set()
        results: list[ProjectionResult] = []
        for event in events:
            if event.event_id in self._processed_event_ids:
                results.append(
                    ProjectionResult(event_id=event.event_id, replay=True, applied=False)
                )
                continue
            apply_projection(self.projection, event)
            self._processed_event_ids.add(event.event_id)
            results.append(ProjectionResult(event_id=event.event_id, replay=True, applied=True))
        return tuple(results)
