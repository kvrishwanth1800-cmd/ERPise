# WO-6 context and accepted constraints

## Fixed upstream constraints

- WO-1 supplies PostgreSQL as the local foundation service. No production credentials are used.
- WO-2 quality gates remain mandatory. Persistent integration tests must provide their required infrastructure in CI.
- WO-3 tenant ownership remains a server-side boundary. Operations evidence reads, writes, indexes, and retention are tenant scoped.
- WO-4 keeps authorization as a separate future boundary. This work does not add an evidence API.
- WO-5 preserves append-only audit evidence. Operations evidence is likewise written as new records.
- WO-8 defines v1 event envelopes. This work stores event ID, type, version, occurrence time, and trace ID without changing the contract.
- WO-9 commits operational state before publish and uses replay-safe consumers. This work records trace-to-event evidence without publishing or replaying business effects.

## Migration and rollback

- `0001_operations_evidence.up.sql` creates operations evidence and alert tables with tenant/trace and tenant/event indexes.
- `0001_operations_evidence.down.sql` removes only those tables and is safe before any dependent schema is introduced.
- No existing data, domain table, or event record is changed.

## Future integration constraints

- WO-7 and WO-25 must preserve tenant scope, trace context, and v1 event identity in their operational evidence.
- A future authorized Operations Evidence API must enforce WO-4 authorization before exposing evidence.
- A future external alert provider must consume only redacted telemetry and persisted actionable alerts.
