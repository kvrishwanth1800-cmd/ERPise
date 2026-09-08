# WO-14 verification log

## Validated commit

- Implementation commit: `c1b3ddbb86acab6c28bb699949e6161886e8ae3d`.
- All seven repository workflow gates completed successfully. No failed or active runs remained at closure.

## Acceptance mapping

- AC-RES-001.1: Concurrent reservation attempts for the last unit are serialized at the reservation-engine transaction boundary. The concurrency test proves exactly one attempt reserves the unit.
- AC-RES-001.2: Outcomes are stored by tenant and idempotency key. A retry returns its first stored outcome without another availability update or event.
- AC-RES-001.3: Release and expiry restore availability once. Commit finalizes the reservation without restoring availability. Repeated calls return the existing final result without a second update.
- AC-RES-001.4: Reject-partial requests return a `Conflict` outcome with available quantity. Allow-partial requests reserve the eligible remainder.

## Review conclusion

The transaction-core implementation is limited to the ReservationEngine domain service. It owns synchronized transitions and reservation facts, and exposes `ReservationChangedEvent` facts for the transaction-service integration boundary. The reservation API and channel-specific adapters remain outside this work order. Result: GO for receiving and putaway work.
