# WO-8 Context

## Work order

Define versioned shared contracts for the Event Platform. It blocks WO-9 transactional outbox and replay.

## Fulfillment mapping

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Preserve compatibility for supported consumers when event contracts change | `CONTRACT_VERSION_V1`, `DomainEvent<TPayload>`, and supported-version validation | Valid v1 event test and unsupported-version rejection test |
| Safe boundary errors | `ProblemDetail` and typed validation results | Missing required field and unsupported-version tests |
| Command deduplication context | Required `idempotencyKey` in `CommandEnvelope<TPayload>` | Missing idempotency-key rejection test |
| Traceability | Required `traceId` in command and event envelopes | Missing event trace-field rejection test |

## Accepted downstream contract

WO-9 must preserve `version`, `eventId`, `eventType`, `occurredAt`, `traceId`, and `payload` when recording and replaying domain events. It must preserve command idempotency keys where commands enter the outbox path.
