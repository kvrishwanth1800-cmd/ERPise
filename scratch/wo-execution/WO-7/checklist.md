# WO-7 Checklist

## Delivery Manager

- [x] WO-6 dependency evidence is complete.
- [x] Encryption, device trust, reconciliation, retry, freshness, rotation, rollback, and telemetry decisions are approved.
- [ ] All acceptance criteria have passing evidence.
- [ ] Implementation and evidence commits pass mandatory validation.

## Implementation

- [ ] Define versioned edge contracts.
- [ ] Add an `edge-sync` Rust crate with SQLCipher storage.
- [ ] Persist tenant, site, register, device, sequence, command, idempotency, trace, causation, and correlation context.
- [ ] Enforce credential binding and revocation.
- [ ] Implement ordered synchronization, reconciliation outcomes, cursor rules, deterministic retry, controlled recovery, and freshness.
- [ ] Record durable local audit evidence and redact telemetry.
- [ ] Implement forward migration and recovery-safe rollback guard.

## Verification

- [ ] Contract tests pass.
- [ ] Unit, failure-path, restart, duplicate, ordering, replay, migration, and rollback tests pass.
- [ ] Formatting, linting, and type checks pass.
- [ ] Delivery Manager, Tech Lead, and Clean-Code Optimizer sign-offs are recorded.
