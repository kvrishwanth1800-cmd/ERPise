# WO-9 implementation plan

## Goal

Implement an in-memory transactional outbox baseline that keeps a successful state change and its event record together, delivers only pending records, and supports deduplicated consumption and replay-safe projections.

## Delivery slice

1. Define immutable event, outbox-record, and projection-result models.
2. Stage a managed state change. Append the event record only after the operation succeeds.
3. Deliver pending records and mark them delivered only after the publisher callback returns.
4. Deduplicate consumer effects by event identifier.
5. Rebuild projections during replay without invoking unsafe-effect callbacks.
6. Validate required event version, identifiers, trace context, and timezone-aware occurrence time.

## Boundaries

- This work is an in-memory foundation baseline.
- It does not add a broker, database persistence, API, migration, module projection, or external provider.
- It preserves the version, event identifier, type, occurrence time, trace identifier, and payload contract established by WO-8.
