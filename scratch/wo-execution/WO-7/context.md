# WO-7 Context

## Authority

- Work order: WO-7, Implement edge synchronization baseline.
- Requirement: Edge and Offline Runtime, REQ-EDG-001.
- Feature blueprint: Edge and Offline Runtime.
- Container blueprint: Counter POS and Store Edge.
- Upstream baseline: WO-6 evidence commit `68198e5aa25a57e31a16959df801e99c19df60c3`.

## Boundaries

Rust owns store-edge synchronization. PostgreSQL remains the authoritative central operational store. The edge runtime does not make direct cross-context database writes. It stores only local, encrypted operational state and sends versioned commands through the central boundary.

## Approved Decisions

1. SQLCipher encrypts the full local database. The per-device key comes only from an operating-system secure store.
2. A centrally enrolled device credential is bound to tenant, site, register, and device identity. Revocation blocks new work and synchronization, while preserving queued work for controlled recovery.
3. Reconciliation outcomes are `ACCEPTED`, `DUPLICATE`, `RETRYABLE_FAILURE`, and `CONTROLLED_RECOVERY`. Only accepted and duplicate results advance the cursor.
4. Retry uses deterministic capped exponential backoff. Retry exhaustion moves work to controlled recovery.
5. Connectivity loss is immediately `OFFLINE`. A delayed successful synchronization produces `STALE`, with the last-success timestamp.
6. Key rotation uses controlled re-enrollment. No manual key export, import, plaintext recovery, or shared secret is permitted.
7. Local audit evidence is durable. Redacted telemetry may synchronize through the operations-evidence boundary when connected.
