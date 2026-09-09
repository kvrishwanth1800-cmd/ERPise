# WO-7 Implementation Plan

## Scope

Implement the Rust edge synchronization baseline. It owns encrypted local queue storage, device and register authorization, freshness state, ordered reconciliation, retry state, and controlled recovery. It does not implement the full POS transaction interface or central sale, payment, inventory, or order services.

## Approved Security Design

- Store operational data in an SQLCipher-encrypted local database.
- Generate a random database key per enrolled device. A platform secure-store adapter supplies the key at database open.
- Never persist keys in source control, configuration, logs, telemetry, backups, exports, event streams, or synchronization payloads.
- Bind each device credential to exactly one tenant, site, register, and device identity.
- A known revoked or mismatched credential blocks new local work and all new synchronization. Existing operations remain encrypted and preserved for supervised recovery.
- Re-enrollment creates a new key. It does not export or recover plaintext data or shared secrets.

## Contracts and State

- `SaleCommand` is an offline-permitted command payload with command, trace, causation, correlation, and idempotency identifiers.
- `EdgeSyncEnvelope` adds tenant, site, register, device, sequence, and retry context.
- `EdgeOperationReconciled` reports `ACCEPTED`, `DUPLICATE`, `RETRYABLE_FAILURE`, or `CONTROLLED_RECOVERY`.
- The durable cursor advances only for `ACCEPTED` and `DUPLICATE` results.
- Pending work is ordered by durable sequence. Retries use deterministic capped exponential backoff. Exhausted retries enter controlled recovery.
- The runtime reports `OFFLINE` immediately after connectivity loss and `STALE` after the configured time since a successful synchronization.

## Storage and Migration

- The `edge-sync` crate owns its SQLCipher schema and schema-version record.
- A forward migration creates only edge-owned tables: device binding, queued operations, reconciliation records, audit records, and cursor state.
- Rollback is tested as a recovery-safe operation. It must not silently delete pending work. Schema removal requires a controlled-recovery precondition when queued records exist.

## Verification

Run Rust formatting, Clippy with warnings denied, Rust tests, TypeScript formatting, lint, type checks, and contract tests. Tests cover encrypted database creation and restart, authorization and revocation, duplicate and out-of-order results, cursor advancement, retry exhaustion, freshness states, migration rollback guard, redaction, and replay safety.

## Threat Model and Recovery

An attacker with only the database file must not access operational records. A missing, corrupted, invalidated, or mismatched secure-store key prevents database access and requires re-enrollment. A revoked device preserves its encrypted queue and audit trail for supervised recovery; it cannot create or synchronize new work. Recovery does not use plaintext exports or shared secrets.
