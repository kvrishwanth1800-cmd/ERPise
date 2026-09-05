# WO-9 context and accepted constraints

## Upstream inputs

- WO-8 provides the v1 event-envelope baseline: version, event identifier, event type, occurred-at time, trace identifier, and payload.
- The Event Platform requirement requires availability of committed facts, one logical effect for duplicates, and replay without unsafe external effects.
- Existing foundation services are in-memory baselines. This work keeps that scope.

## Accepted constraints

- State and the outbox record are committed together only after the supplied operation succeeds.
- A publisher callback can be retried while a record remains pending. A record is marked delivered only after that callback returns.
- Consumers use event identifiers to suppress duplicate logical effects.
- Replay resets the projection and deduplication state, then rebuilds only projection data. It does not invoke unsafe-effect callbacks.
- Events require a version, event identifier, event type, trace identifier, and timezone-aware occurrence time.
- No persistent outbox store, message broker, API endpoint, schema migration, production credential, or irreversible action is included.

## Downstream constraints

- WO-6 must retain the trace identifier from request and event evidence through recovery and release observability.
- WO-10 and later module work must use stable event identifiers for consumer deduplication and must treat replay as projection-only processing.
- A future persistence or broker work order must preserve pending-to-delivered semantics and must not alter committed event records.
