# WO-4 context

## Work order boundary

WO-4 completes encrypted local edge synchronization recovery. It covers durable offline queueing, ordered reconnect dispatch through an injected BFF boundary, duplicate-safe reconciliation, retry and restart recovery, controlled recovery, scope isolation, freshness state, and focused evidence.

It does not introduce a network transport, production credentials, or central durable-outbox behavior. The central durable outbox remains WO-31 scope.

## Dependency and configuration findings

- The Rust workspace members are `transaction-core`, `edge-sync`, and `edge-sync-reconciliation`.
- `edge-sync` uses bundled SQLCipher through `rusqlite`. The reconciliation crate depends only on `edge-sync`; it has no generated sources, build scripts, macro-expansion path, or additional runtime dependency.
- `rustfmt.toml` sets edition 2021 and `max_width = 120`.
- A prior CI job used moving Rust stable 1.98.1. Rust, Cargo, Clippy, and rustfmt are now pinned to 1.85.0, the workspace minimum, by `rust-toolchain.toml` and the focused workflow.
- The pinned formatter diagnostic passed in a clean Actions checkout without a restored source cache. The earlier formatter failure is therefore recorded as toolchain drift. The historical 1.98.1 generated diff was not retained by the older job logs.
- The complete Clippy diagnostic identified `clippy::drop_non_drop` at `crates/edge-sync-reconciliation/src/lib.rs:152`. This is RWO-4-owned test code. It is unrelated to SQLCipher, the BFF boundary, or any third-party dependency.

## Source documents

- Counter POS and Store Edge blueprint
- Edge and Offline Runtime blueprint
- Transactional Outbox and Replay blueprint
