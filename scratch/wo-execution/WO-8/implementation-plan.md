# WO-8 Implementation Plan

## Scope

Define the version 1 shared command, event, error, health, and trace boundary contracts. Business command handlers are out of scope.

## Increment

1. Add the v1 contract version and generic command and event envelopes.
2. Add typed non-retryable problem details.
3. Validate version, identifiers, trace IDs, and command idempotency keys at the boundary.
4. Add compatibility and rejected-input tests.

## Constraints carried forward

- Contracts are versioned at the boundary.
- Unsupported versions fail safely and are not retryable.
- Commands require trace and idempotency keys.
- Events require trace IDs.
- This work does not add persistence, dispatch, replay, or business handlers.

## Rollback

Revert this feature branch commit series. No migration, deployment, external provider, or persistent data change is included.
