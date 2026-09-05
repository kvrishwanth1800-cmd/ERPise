# WO-4 context

## Work order boundary

WO-4 completes encrypted local edge synchronization recovery. It covers durable offline queueing, ordered reconnect dispatch through an injected BFF boundary, duplicate-safe reconciliation, retry and restart recovery, controlled recovery, scope isolation, freshness state, and focused evidence.

It does not introduce a network transport, production credentials, or central durable-outbox behavior. The central durable outbox remains WO-31 scope.

## Dependency and configuration findings

- The Rust workspace members are `transaction-core`, `edge-sync`, and `edge-sync-reconciliation`.
- `edge-sync` uses bundled SQLCipher through `rusqlite`. The reconciliation crate depends only on `edge-sync` and has no generated sources or macro-expansion build step.
- The repository has `rustfmt.toml` with edition 2021 and `max_width = 120`.
- Before this analysis, CI selected moving `stable`, which installed Rust 1.98.1. No `rust-toolchain.toml` existed, so local and CI formatter versions were not pinned or proven aligned.
- Repeated formatter failures were isolated to `edge-sync-reconciliation`. `edge-sync` formatting and all focused test commands passed. The CI output did not expose the required formatter diff.
- RWO-4 now pins Rust 1.85.0, the workspace declared minimum Rust version, and captures formatter check, formatter apply, diff, status, and recheck evidence in CI before any further code remediation.

## Source documents

- Counter POS and Store Edge blueprint
- Edge and Offline Runtime blueprint
- Transactional Outbox and Replay blueprint
